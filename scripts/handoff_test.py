"""任务交接关键路径：创建、并发保护、证据核验、拒绝、过期和 CLI。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
os.environ["MEMORY_DATA_DIR"] = tempfile.mkdtemp(prefix="handoff_db_")
os.environ["HANDOFF_DB_PATH"] = str(Path(os.environ["MEMORY_DATA_DIR"]) / "test.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.db import get_conn  # noqa: E402
from app.handoff_artifacts import prepare_handoff, verify_handoff  # noqa: E402
from app.main import app  # noqa: E402
from app.models import HandoffCreate  # noqa: E402

workspace = Path(tempfile.mkdtemp(prefix="handoff_workspace_"))
(workspace / "fix.py").write_text("print('fixed')\n", encoding="utf-8")
payload = {
    "task_id": "bug-1", "from_agent": "a", "to_agent": "b",
    "goal": "修复错误", "status_summary": "已修改，待验证",
    "next_steps": ["运行回归测试"],
    "artifacts": [{"path": "fix.py", "note": "补丁"}],
    "validation": [{"command": "python fix.py", "outcome": "not_run"}],
}
body = prepare_handoff(HandoffCreate.model_validate(payload), workspace)

with TestClient(app) as client:
    created = client.post("/v1/handoffs", json=body.model_dump(mode="json"))
    assert created.status_code == 200, created.text
    handoff = created.json()
    hid = handoff["id"]
    assert handoff["status"] == "pending"
    assert verify_handoff(handoff, workspace)["ok"]

    assert client.post(f"/v1/handoffs/{hid}/claim", json={"agent_id": "wrong"}).status_code == 409
    claim = client.post(f"/v1/handoffs/{hid}/claim", json={"agent_id": "b"})
    assert claim.status_code == 200, claim.text
    assert claim.json()["status"] == "claimed"
    assert client.post(f"/v1/handoffs/{hid}/claim", json={"agent_id": "b"}).status_code == 200
    assert client.post(f"/v1/handoffs/{hid}/claim", json={"agent_id": "c"}).status_code == 409
    assert client.post(f"/v1/handoffs/{hid}/ack", json={"agent_id": "c", "accepted": True}).status_code == 409
    assert client.post(f"/v1/handoffs/{hid}/ack", json={"agent_id": "b", "accepted": False}).status_code == 409

    (workspace / "fix.py").write_text("print('changed')\n", encoding="utf-8")
    assert not verify_handoff(handoff, workspace)["ok"]
    (workspace / "fix.py").write_text("print('fixed')\n", encoding="utf-8")
    assert verify_handoff(handoff, workspace)["ok"]
    accepted = client.post(f"/v1/handoffs/{hid}/ack", json={"agent_id": "b", "accepted": True})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert client.post(f"/v1/handoffs/{hid}/complete", json={"agent_id": "a"}).status_code == 409
    assert client.post(f"/v1/handoffs/{hid}/complete", json={"agent_id": "b"}).json()["status"] == "completed"

    # 租约到期可由目标 agent 重新领取。
    other = dict(payload, task_id="bug-2", to_agent=None)
    hid2 = client.post("/v1/handoffs", json=other).json()["id"]
    assert client.post(f"/v1/handoffs/{hid2}/claim", json={"agent_id": "x"}).status_code == 200
    with get_conn() as conn:
        conn.execute("UPDATE handoffs SET claim_until = '2000-01-01' WHERE id = ?", (hid2,))
    assert client.post(f"/v1/handoffs/{hid2}/claim", json={"agent_id": "y"}).json()["claimed_by"] == "y"

    hid3 = client.post("/v1/handoffs", json=dict(payload, task_id="bug-3")).json()["id"]
    with get_conn() as conn:
        conn.execute("UPDATE handoffs SET expires_at = '2000-01-01' WHERE id = ?", (hid3,))
    assert client.get(f"/v1/handoffs/{hid3}").json()["status"] == "expired"
    assert client.post(f"/v1/handoffs/{hid3}/claim", json={"agent_id": "b"}).status_code == 409

    rows = client.get("/v1/handoffs", params={"task_id": "bug-1"}).json()["results"]
    assert len(rows) == 1 and rows[0]["status"] == "completed"

# CLI 使用相同存储与交接包格式。
source = workspace / "handoff.json"
source.write_text(json.dumps(dict(payload, task_id="bug-cli"), ensure_ascii=False), encoding="utf-8")
markdown = workspace / "handoff.md"
env = {**os.environ, "PYTHONPATH": str(ROOT / "server")}
result = subprocess.run(
    [sys.executable, "-m", "app.handoff_cli", "create", str(source),
     "--workspace", str(workspace), "--markdown", str(markdown)],
    env=env, text=True, capture_output=True, check=True,
)
cli_handoff = json.loads(result.stdout)
assert markdown.exists() and "下一步" in markdown.read_text(encoding="utf-8")
result = subprocess.run(
    [sys.executable, "-m", "app.handoff_cli", "verify", cli_handoff["id"],
     "--workspace", str(workspace)], env=env, text=True, capture_output=True, check=True,
)
assert json.loads(result.stdout)["ok"]

# 路径不能越过工作区；没有任何可机械核验的证据不能声称通过。
try:
    prepare_handoff(HandoffCreate.model_validate(dict(payload, artifacts=[{"path": "../outside"}])), workspace)
except ValueError:
    pass
else:
    raise AssertionError("path traversal was accepted")
assert not verify_handoff({"payload": {"artifacts": []}}, workspace)["ok"]

print("handoff tests: passed")
