"""P0 冒烟测试：记忆五件套 + 混合检索 + 消息摄入 + 总结管道端到端（fake provider）。

运行：python scripts/smoke_test.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
os.environ["MEMORY_DATA_DIR"] = tempfile.mkdtemp(prefix="mem_test_")
os.environ["HANDOFF_DB_PATH"] = os.path.join(os.environ["MEMORY_DATA_DIR"], "test.db")
os.environ["MEMORY_LLM_PROVIDER"] = "fake"  # 内置规则模型，零成本端到端

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
passed = failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


with client:
    print("== health ==")
    r = client.get("/health")
    check("health", r.status_code == 200 and r.json()["status"] == "ok")

    print("== memory 五件套 ==")
    r = client.post("/v1/memory", json={
        "agent_id": "agent_a",
        "content": "用户偏好简洁的结构化中文输出，使用表格和全角引号",
        "tags": ["preference", "style"],
    })
    check("store", r.status_code == 200, r.text)
    mid = r.json()["id"]

    r = client.post("/v1/memory", json={
        "agent_id": "agent_b",
        "content": "任务：搭建多 agent 记忆管理系统 P0 骨架",
        "layer": "episodic",
    })
    check("store agent_b", r.status_code == 200)

    r = client.post("/v1/memory/search", json={"query": "结构化 输出", "agent_id": "agent_a"})
    check("search fts", r.status_code == 200 and len(r.json()["results"]) >= 1, r.text)

    r = client.post("/v1/memory/search", json={"query": "记忆管理系统"})
    check("search cross-agent", r.status_code == 200 and
          any(m["agent_id"] == "agent_b" for m in r.json()["results"]), r.text)

    # 向量检索：query 与目标不完全字面匹配，但字符重合度高时应被向量路召回
    r = client.post("/v1/memory/search", json={"query": "结构化中文输出表格引号偏好"})
    check("search hybrid (vector recall)",
          r.status_code == 200 and any(m["id"] == mid for m in r.json()["results"]), r.text)

    r = client.post("/v1/memory/search", json={"query": "", "agent_id": "agent_a"})
    check("search empty query -> recent", r.status_code == 200 and len(r.json()["results"]) >= 1)

    r = client.get(f"/v1/memory/{mid}")
    check("get", r.status_code == 200 and r.json()["id"] == mid)

    r = client.patch(f"/v1/memory/{mid}", json={"tags": ["preference", "style", "verified"]})
    check("update", r.status_code == 200 and "verified" in r.json()["tags"], r.text)

    r = client.post("/v1/memory/search", json={"query": "", "tags": ["verified"]})
    check("search by tag", r.status_code == 200 and
          any(m["id"] == mid for m in r.json()["results"]), r.text)

    print("== 总结管道端到端（fake provider）==")
    msgs = [
        {"session_id": "s1", "agent_id": "agent_a", "role": "user",
         "content": "帮我设计一个多 agent 记忆管理系统"},
        {"session_id": "s1", "agent_id": "agent_a", "role": "assistant",
         "content": "好的，建议先做本地 P0 记忆管理"},
        {"session_id": "s1", "agent_id": "agent_a", "role": "user",
         "content": "跨设备共享放到最后一期再做"},
        {"session_id": "s1", "agent_id": "agent_a", "role": "assistant",
         "content": "明白，已在架构上预留同步接口"},
    ]
    r = client.post("/v1/messages/batch", json={"messages": msgs})
    check("messages batch", r.status_code == 200 and len(r.json()["added"]) == 4, r.text)

    r = client.post("/v1/summary/trigger", json={"session_id": "s1", "agent_id": "agent_a"})
    body = r.json()
    check("summary trigger ok", r.status_code == 200 and body["status"] == "ok", r.text)
    check("facts extracted", body.get("facts_added", 0) >= 1, str(body))
    check("summary non-empty", bool(body.get("summary")), str(body))
    check("episodic snapshot", bool(body.get("episodic_id")), str(body))

    r = client.get("/v1/summary", params={"agent_id": "agent_a", "session_id": "s1"})
    check("summary get", r.status_code == 200 and r.json()["summary"] is not None, r.text)

    # 增量语义：同一 session 再次触发应跳过（无未总结消息）
    r = client.post("/v1/summary/trigger", json={"session_id": "s1", "agent_id": "agent_a"})
    check("re-trigger skipped (no new messages)", r.json()["status"] == "skipped", r.text)

    # 提炼出的事实可被检索到
    r = client.post("/v1/memory/search", json={"query": "记忆管理系统", "layer": "semantic"})
    check("facts searchable", r.status_code == 200 and len(r.json()["results"]) >= 1, r.text)

    print("== 无 provider 时的优雅降级 ==")
    from app import config as _config
    saved = _config.LLM_PROVIDER
    try:
        _config.LLM_PROVIDER = ""
        _config.LLM_BASE_URL = ""
        client.post("/v1/messages", json={
            "session_id": "s2", "agent_id": "agent_a", "role": "user", "content": "测试降级路径"})
        r = client.post("/v1/summary/trigger",
                        json={"session_id": "s2", "agent_id": "agent_a", "force": True})
        check("no provider -> skipped", r.json()["status"] == "skipped", r.text)
    finally:
        _config.LLM_PROVIDER = saved

    print("== delete ==")
    r = client.delete(f"/v1/memory/{mid}")
    check("delete", r.status_code == 200)
    r = client.get(f"/v1/memory/{mid}")
    check("get after delete -> 404", r.status_code == 404)

print(f"\n结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
