"""MCP stdio 端到端测试；需要安装项目的 mcp 可选依赖。"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
workspace = Path(tempfile.mkdtemp(prefix="handoff_mcp_workspace_"))
(workspace / "work.txt").write_text("ready\n", encoding="utf-8")
env = {**os.environ, "MEMORY_DATA_DIR": tempfile.mkdtemp(prefix="handoff_mcp_db_"),
       "PYTHONPATH": str(ROOT / "server") + os.pathsep + os.environ.get("PYTHONPATH", "")}
env["HANDOFF_DB_PATH"] = str(Path(env["MEMORY_DATA_DIR"]) / "test.db")


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.handoff_mcp"], env=env
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"handoff_create", "handoff_claim", "handoff_verify", "handoff_ack"} <= names
        payload = {
            "task_id": "mcp-smoke", "from_agent": "a", "to_agent": "b",
            "goal": "继续处理任务", "status_summary": "已创建工作文件",
            "artifacts": [{"path": "work.txt"}],
        }
        created = await client.call_tool("handoff_create", {
            "payload": payload, "workspace": str(workspace)
        })
        assert not created.is_error, created
        handoff_id = (created.structured_content or json.loads(created.content[0].text))["id"]
        claimed = await client.call_tool("handoff_claim", {"handoff_id": handoff_id, "agent_id": "b"})
        assert (claimed.structured_content or json.loads(claimed.content[0].text))["status"] == "claimed", claimed
        verified = await client.call_tool("handoff_verify", {
            "handoff_id": handoff_id, "workspace": str(workspace)
        })
        assert (verified.structured_content or json.loads(verified.content[0].text))["ok"], verified
        accepted = await client.call_tool("handoff_ack", {
            "handoff_id": handoff_id, "agent_id": "b", "accepted": True
        })
        assert (accepted.structured_content or json.loads(accepted.content[0].text))["status"] == "accepted", accepted
    print("MCP stdio handoff: passed")


if __name__ == "__main__":
    asyncio.run(main())
