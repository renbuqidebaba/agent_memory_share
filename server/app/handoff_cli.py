"""本地任务交接命令行。运行：python -m app.handoff_cli --help。"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import __version__
from .maintenance import backup_database, export_data, purge_data
from .handoff_artifacts import prepare_handoff, render_markdown, verify_handoff
from .models import HandoffCreate
from .storage.handoff_store import HandoffConflict, HandoffStore


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-handoff", description="创建、领取并核验 agent 任务交接包")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="从 JSON 文件创建交接包，并固定工作区产物摘要")
    create.add_argument("file", type=Path)
    create.add_argument("--workspace", type=Path, default=Path.cwd())
    create.add_argument("--markdown", type=Path, help="额外导出人可读的 Markdown")
    create.add_argument("--capture", action="store_true", help="自动采集现场并要求核验记录")
    create.add_argument("--include", action="append", default=[], help="明确纳入的未跟踪文件，可重复")
    create.add_argument("--check-id", action="append", default=[], help="关联 run-check 返回的实测记录 ID")
    listing = sub.add_parser("list", help="列出交接包")
    listing.add_argument("--task-id")
    listing.add_argument("--status")
    listing.add_argument("--to-agent")
    listing.add_argument("--workspace", type=Path)
    show = sub.add_parser("show", help="查看交接包")
    show.add_argument("id")
    show.add_argument("--markdown", action="store_true")
    claim = sub.add_parser("claim", help="领取交接包")
    claim.add_argument("id")
    claim.add_argument("--agent", required=True)
    claim.add_argument("--lease-seconds", type=int, default=3600)
    verify = sub.add_parser("verify", help="核对 Git HEAD 和文件摘要；不会执行测试命令")
    verify.add_argument("id")
    verify.add_argument("--workspace", type=Path, default=Path.cwd())
    verify.add_argument("--agent", help="保存属于当前领取者的核验记录")
    ack = sub.add_parser("ack", help="核验后接受或退回交接")
    ack.add_argument("id")
    ack.add_argument("--agent", required=True)
    choice = ack.add_mutually_exclusive_group(required=True)
    choice.add_argument("--accept", action="store_true")
    choice.add_argument("--reject", action="store_true")
    ack.add_argument("--reason", default="")
    ack.add_argument("--verification-id")
    complete = sub.add_parser("complete", help="标记任务已由接收者完成")
    complete.add_argument("id")
    complete.add_argument("--agent", required=True)
    checks = sub.add_parser("run-check", help="执行明确给定的命令并保存退出码和日志；不读取交接包命令")
    checks.add_argument("--workspace", type=Path, default=Path.cwd())
    checks.add_argument("--timeout", type=int, default=120)
    checks.add_argument("--include", action="append", default=[])
    checks.add_argument("argv", nargs=argparse.REMAINDER)
    receipts = sub.add_parser("verifications", help="查看交接核验记录")
    receipts.add_argument("id")
    sub.add_parser("schema", help="输出交接包 JSON Schema；不访问数据库")
    sub.add_parser("export", help="向 stdout 导出全部交接包与交接事件 JSON")
    backup = sub.add_parser("backup", help="创建一致的 SQLite 备份，拒绝覆盖文件")
    backup.add_argument("file", type=Path)
    purge = sub.add_parser("purge", help="预览清理终态交接包；加 --apply 才会删除")
    purge.add_argument("--older-than-days", type=int, default=30)
    purge.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            _json(HandoffCreate.model_json_schema())
            return 0
        if args.command in ("export", "backup", "purge"):
            if args.command == "export":
                result = export_data()
            elif args.command == "backup":
                result = backup_database(args.file)
            else:
                result = purge_data(args.older_than_days, apply=args.apply)
            _json(result)
            return 0
        store = HandoffStore()
        if args.command == "create":
            body = HandoffCreate.model_validate_json(args.file.read_text(encoding="utf-8"))
            if not args.capture and (args.include or args.check_id):
                raise ValueError("--include / --check-id 需要 --capture")
            result = (store.capture(body, args.workspace, args.include, args.check_id) if args.capture
                      else store.create(prepare_handoff(body, args.workspace)))
            if args.markdown:
                args.markdown.write_text(render_markdown(result), encoding="utf-8")
        elif args.command == "list":
            result = store.list(args.task_id, args.status, args.to_agent, args.workspace)
        elif args.command == "run-check":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            result = store.run_check(args.workspace, argv, args.timeout, args.include)
            _json(result)
            return 0 if result["outcome"] == "passed" else 2
        elif args.command == "verifications":
            if store.get(args.id) is None:
                raise KeyError(args.id)
            result = store.verifications(args.id)
        else:
            if args.command in ("show", "verify"):
                result = store.get(args.id)
                if result is None:
                    raise KeyError(args.id)
                if args.command == "verify":
                    result = (store.verify(args.id, args.agent, args.workspace) if args.agent
                              else verify_handoff(result, args.workspace))
                elif args.markdown:
                    print(render_markdown(result))
                    return 0
            elif args.command == "claim":
                result = store.claim(args.id, args.agent, args.lease_seconds)
            elif args.command == "ack":
                result = store.ack(args.id, args.agent, args.accept, args.reason, args.verification_id)
            else:
                result = store.complete(args.id, args.agent)
        _json(result)
        return 0 if args.command != "verify" or result["ok"] else 2
    except (KeyError, HandoffConflict, ValueError, OSError, sqlite3.Error, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
