"""交接包中的工作区证据：只记录文件摘要，不执行交接包里的命令。"""
import hashlib
import subprocess
from pathlib import Path

from .models import HandoffCreate


def _safe_path(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError(f"产物路径必须是相对路径: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"产物路径越过工作区: {relative}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_handoff(body: HandoffCreate, workspace: str | Path) -> HandoffCreate:
    """创建时固定产物摘要和 Git HEAD；无法访问的文件直接报错。"""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise ValueError(f"工作区不存在: {root}")
    data = body.model_dump()
    data["workspace_root"] = str(root)
    for artifact in data["artifacts"]:
        path = _safe_path(root, artifact["path"])
        if not path.is_file():
            raise ValueError(f"产物文件不存在: {artifact['path']}")
        artifact["sha256"] = _sha256(path)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False,
        env=_git_environment(), timeout=15
    )
    if result.returncode == 0:
        data["source_revision"] = result.stdout.strip()
    else:
        data["source_revision"] = None
    return HandoffCreate.model_validate(data)


def _git_environment():
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def verify_handoff(handoff: dict, workspace: str | Path) -> dict:
    """核对可机械验证的证据；测试结果仍需接手方自行复验。"""
    root = Path(workspace).resolve()
    checks = []
    if not root.is_dir():
        return {"ok": False, "checks": [{"kind": "workspace", "ok": False, "detail": str(root)}]}
    payload = handoff["payload"]
    if payload.get("schema_version", 1) not in (1, 2):
        return {"ok": False, "checks": [{"kind": "schema_version", "ok": False,
                "detail": "Unsupported handoff schema_version"}]}
    if payload.get("schema_version") == 2 and not payload.get("require_verification"):
        return {"ok": False, "checks": [{"kind": "verification_policy", "ok": False,
                "detail": "v2 requires a verification receipt"}]}
    expected = payload.get("source_revision")
    if expected:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False,
            env=_git_environment(), timeout=15
        )
        current = result.stdout.strip() if result.returncode == 0 else None
        checks.append({"kind": "git_revision", "ok": current == expected,
                       "expected": expected, "actual": current})
    for artifact in payload.get("artifacts", []):
        name = artifact["path"]
        try:
            path = _safe_path(root, name)
            actual = _sha256(path) if path.is_file() else None
            expected = artifact.get("sha256")
            checks.append({"kind": "artifact", "path": name, "ok": bool(expected) and actual == expected,
                           "expected": expected, "actual": actual})
        except (ValueError, OSError) as exc:
            checks.append({"kind": "artifact", "path": name, "ok": False, "detail": str(exc)})
    if payload.get("workspace_snapshot"):
        from .workspace_capture import verify_snapshot
        checks.extend(verify_snapshot(payload["workspace_snapshot"], root)["checks"])
    return {"ok": bool(checks) and all(check["ok"] for check in checks), "checks": checks,
            "note": "文件与 Git 版本核验不代表测试结果仍然有效；请重新运行关键验证。"}


def render_markdown(handoff: dict) -> str:
    p = handoff["payload"]
    lines = [f"# 任务交接：{p['task_id']}", "", f"- 交接 ID：`{handoff['id']}`",
             f"- 状态：{handoff['status']}", f"- 发送者：{p['from_agent']}",
             f"- 接收者：{p.get('to_agent') or '开放领取'}", "", "## 目标", "", p["goal"],
             "", "## 当前状态", "", p["status_summary"]]
    sections = [("完成标准", "done_when"), ("已完成", "completed"),
                ("关键决策", "decisions"), ("下一步", "next_steps"),
                ("待解决问题", "open_questions")]
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {item}" for item in p.get(key, []))
        if not p.get(key):
            lines.append("- 暂无记录")
    lines.extend(["", "## 产物", ""])
    for item in p.get("artifacts", []):
        lines.append(f"- `{item['path']}` — {item.get('note') or '无说明'}；SHA-256: `{item.get('sha256') or '未记录'}`")
    if not p.get("artifacts"):
        lines.append("- 暂无记录")
    lines.extend(["", "## 验证记录", ""])
    for item in p.get("validation", []):
        lines.append(f"- `{item['command']}`：{item['outcome']}；{item.get('detail') or '无详情'}")
    if not p.get("validation"):
        lines.append("- 暂无记录")
    lines.extend(["", f"Git HEAD：`{p.get('source_revision') or '未记录'}`",
                  "", "接手时请核对产物摘要并重新运行关键验证。", ""])
    snapshot = p.get("workspace_snapshot")
    if snapshot:
        lines.extend(["## 工具采集的现场", "", f"- 采集时间：{snapshot['captured_at']}",
                      f"- 文件数：{len(snapshot['files'])}",
                      f"- 排除的路径：{', '.join(snapshot['excluded']) or '无'}",
                      f"- 未纳入的未跟踪文件：{', '.join(snapshot['untracked_not_included']) or '无'}",
                      f"- Diff 已截断：{snapshot['diff_truncated']}", ""])
    for check in p.get("observed_checks", []):
        lines.append(f"- 实测检查 `{check['id']}`：{check['outcome']}，退出码 {check['exit_code']}")
    return "\n".join(lines)
