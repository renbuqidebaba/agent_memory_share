"""经官方客户端启动独立 stdio 服务验证 MCP 协议入口。"""
import importlib.util
import functools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def connected(method):
    @functools.wraps(method)
    async def run(self):
        # AnyIO 的会话必须在同一个 asyncio task 中进入和退出。
        async with Client(self.params) as client:
            self.client = client
            return await method(self)
    return run


@unittest.skipUnless(HAS_MCP, "未安装 MCP 可选依赖")
class McpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="handoff_mcp_case_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "work.txt"
        self.file.write_text("evidence")
        env = {**os.environ, "MEMORY_DB_PATH": str(self.root / "db" / "test.db"),
               "HANDOFF_DB_PATH": str(self.root / "db" / "test.db"),
               "MEMORY_DATA_DIR": str(self.root / "db"),
               "PYTHONPATH": str(ROOT / "server") + os.pathsep + os.environ.get("PYTHONPATH", "")}
        self.params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.handoff_mcp"], env=env
        )

    async def call(self, name, **args):
        result = await self.client.call_tool(name, args)
        self.assertFalse(result.is_error, str(result))
        value = result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)
        # SDK 对 list 返回值使用 result 包装，dict 返回值直接输出。
        return value["result"] if isinstance(value, dict) and set(value) == {"result"} else value

    async def create(self):
        return await self.call("handoff_create", workspace=str(self.root), payload={
            "task_id": "mcp-task", "from_agent": "alice", "goal": "继续工作",
            "status_summary": "待接手", "artifacts": [{"path": "work.txt"}],
        })

    @connected
    async def test_discovery_exposes_structured_schema(self):
        """MCP01 握手及工具发现成功，创建工具暴露结构化参数。"""
        result = await self.client.list_tools()
        tools = {t.name: t for t in result.tools}
        self.assertEqual(set(tools), {"handoff_create", "handoff_capture", "handoff_run_check", "handoff_list", "handoff_get", "handoff_claim", "handoff_verify", "handoff_ack", "handoff_complete"})
        self.assertIn("payload", tools["handoff_create"].input_schema["properties"])

    @connected
    async def test_stdio_complete_lifecycle(self):
        """MCP02 独立 stdio 进程完整交接与查询。"""
        h = await self.create()
        hid = h["id"]
        self.assertEqual((await self.call("handoff_claim", handoff_id=hid, agent_id="bob"))["status"], "claimed")
        self.assertTrue((await self.call("handoff_verify", handoff_id=hid, workspace=str(self.root)))["ok"])
        self.assertEqual((await self.call("handoff_ack", handoff_id=hid, agent_id="bob", accepted=True))["status"], "accepted")
        self.assertEqual((await self.call("handoff_complete", handoff_id=hid, agent_id="bob"))["status"], "completed")
        self.assertEqual((await self.call("handoff_get", handoff_id=hid))["status"], "completed")
        self.assertIn(hid, json.dumps(await self.call("handoff_list", task_id="mcp-task")))

    @connected
    async def test_conflict_is_tool_error_and_server_survives(self):
        """MCP03 重复竞争领取产生工具错误，服务仍可继续处理请求。"""
        hid = (await self.create())["id"]
        await self.call("handoff_claim", handoff_id=hid, agent_id="bob")
        result = await self.client.call_tool("handoff_claim", {"handoff_id": hid, "agent_id": "eve"})
        self.assertTrue(result.is_error)
        self.assertIn("不可领取", str(result.content))
        self.assertEqual((await self.call("handoff_get", handoff_id=hid))["claimed_by"], "bob")

    @connected
    async def test_changed_artifact_fails_verification(self):
        """MCP04 文件被改动后通过 MCP 核验返回失败。"""
        hid = (await self.create())["id"]
        self.file.write_text("tampered")
        self.assertFalse((await self.call("handoff_verify", handoff_id=hid, workspace=str(self.root)))["ok"])

    @connected
    async def test_invalid_create_has_no_side_effect(self):
        """MCP05 非法创建参数报错且不产生交接包。"""
        result = await self.client.call_tool("handoff_create", {"payload": {}, "workspace": str(self.root)})
        self.assertTrue(result.is_error)
        self.assertEqual(await self.call("handoff_list"), [])

    @connected
    async def test_capture_requires_receipt_and_prompts_are_discoverable(self):
        check = await self.call("handoff_run_check", workspace=str(self.root),
            argv=[sys.executable, "-c", "print('measured')"], include=["work.txt"])
        h = await self.call("handoff_capture", workspace=str(self.root), include=["work.txt"],
            check_ids=[check["id"]], payload={"task_id": "captured", "from_agent": "alice",
            "goal": "continue", "status_summary": "ready"})
        self.assertTrue(h["payload"]["require_verification"])
        await self.call("handoff_claim", handoff_id=h["id"], agent_id="bob")
        rejected = await self.client.call_tool("handoff_ack", {
            "handoff_id": h["id"], "agent_id": "bob", "accepted": True})
        self.assertTrue(rejected.is_error)
        receipt = await self.call("handoff_verify", handoff_id=h["id"], workspace=str(self.root), agent_id="bob")
        self.assertTrue(receipt["ok"])
        await self.call("handoff_ack", handoff_id=h["id"], agent_id="bob", accepted=True,
                        verification_id=receipt["verification_id"])
        self.assertEqual(len((await self.call("handoff_get", handoff_id=h["id"]))["verifications"]), 1)
        prompts = await self.client.list_prompts()
        self.assertEqual({p.name for p in prompts.prompts}, {"prepare_handoff", "resume_handoff"})
        prompt = await self.client.get_prompt("resume_handoff", {
            "handoff_id": h["id"], "workspace": str(self.root), "agent_id": "bob"})
        self.assertIn("verification_id", str(prompt))

    @connected
    async def test_missing_resource_is_tool_error(self):
        """MCP06 不存在的交接包明确作为工具错误返回。"""
        for name in ("handoff_get", "handoff_verify"):
            args = {"handoff_id": "missing"}
            if name == "handoff_verify":
                args["workspace"] = str(self.root)
            result = await self.client.call_tool(name, args)
            self.assertTrue(result.is_error)
            self.assertIn("handoff not found", str(result.content))
