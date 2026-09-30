# 多 Agent 记忆管理系统 — 实施方案 v2

> 2026-09-30 | 需求重排：跨设备共享降级为最后一期，先做本地单机的记忆管理 + 记忆迁移

## 一、优先级重排

| 期 | 目标 | 状态 |
|---|---|---|
| **P0** | 多 agent 记忆管理：分 agent 存对话记忆、自动总结提炼、检索注入 | 先做 |
| **P1** | 记忆迁移：agent 间任务上下文的打包、领取、继承（handoff） | 第二阶段 |
| **P2** | 跨设备共享 | **最后做，只在架构上预留接口** |

## 二、核心设计原则：为什么 P2 以后能低成本补上

跨设备难做的根源是「数据一开始就和本机绑死」。只要 P0 就守住三条纪律，P2 时只需加一个同步层，上层零改动：

1. **存储集中在一个 Server 进程内**：agent 永远通过 HTTP API 访问记忆，绝不直接读写本地文件。P2 时把 Server 从 `127.0.0.1` 搬到云上即完成共享。
2. **所有记忆带完整归属元数据**：`agent_id / user_id / session_id / device_id`，P2 的多设备合并不需要补数据。
3. **写入走 append-only 事件日志**：每次写记忆记一条事件（带 Lamport 时间戳或单调递增 seq）。P2 的同步 = 事件日志复制，天然支持离线后再合并。

## 三、总体架构

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │     ← 无状态客户端，只持 HTTP client
└────┬─────┘  └────┬─────┘  └────┬─────┘
     └─────────────┼─────────────┘
              HTTP / JSON (本地 127.0.0.1:8787)
     ┌─────────────▼─────────────┐
     │      Memory Server        │
     │ ┌───────────────────────┐ │
     │ │ API 层                 │ │  memory_* / summary_* / handoff_*
     │ ├───────────────────────┤ │
     │ │ 提炼管道(异步)          │ │  原始对话 → 事实抽取 → 摘要合并
     │ ├───────────────────────┤ │
     │ │ 存储抽象层(接口)        │ │  ← P2 只换这一层的实现
     │ └───────────────────────┘ │
     │   SQLite + sqlite-vec     │     单机零依赖；P2 可换 PG/TiDB
     │   + events.log(追加日志)  │
     └───────────────────────────┘
```

**技术选型**：Python 3.13 + FastAPI + SQLite（+ sqlite-vec 做向量，或先用 FTS5 全文检索，MVP 不强制上 embedding）。理由：单机零运维、一份文件即全部数据、备份=复制文件。embedding 模型走可插拔：本地 Ollama / API 均可。

## 四、数据模型

```sql
-- 记忆主体：三级分层
memories(
  id            TEXT PRIMARY KEY,      -- uuid
  agent_id      TEXT NOT NULL,         -- 归属 agent
  user_id       TEXT,
  session_id    TEXT,                  -- 产生时的会话
  device_id     TEXT,                  -- P2 预留，先填 "local"
  layer         TEXT NOT NULL,         -- episodic(原始片段) / semantic(提炼事实) / summary(摘要)
  content       TEXT NOT NULL,
  tags          TEXT,                  -- JSON array
  embedding     BLOB,                  -- 可空
  valid_from    TEXT,                  -- 时态：生效时间（P1 交接校验用）
  valid_to      TEXT,                  -- 被新事实取代时填
  seq           INTEGER NOT NULL,      -- 事件序号，P2 同步用
  created_at    TEXT NOT NULL
)

-- 会话原始记录（提炼管道的输入，不直接给 agent 读）
messages(
  id TEXT PK, session_id TEXT, agent_id TEXT,
  role TEXT, content TEXT, created_at TEXT
)

-- P1：交接包
handoff_packages(
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL,
  from_agent    TEXT NOT NULL,
  to_agent      TEXT,                  -- 可空 = 公开认领
  status        TEXT NOT NULL,         -- pending / claimed / acked / failed / expired
  payload       TEXT NOT NULL,         -- 结构化四字段 JSON（见 §6）
  memory_refs   TEXT NOT NULL,         -- JSON array of memory_id
  created_at    TEXT, claimed_at TEXT, acked_at TEXT,
  ttl_seconds   INTEGER                -- 超时未 claim 自动 expired
)
```

## 五、P0：记忆管理 + 总结管道

**API（对标 mem9 的五件套 + 两个总结接口）**：

| API | 说明 |
|---|---|
| `memory_store` | 写一条记忆（指定 layer、tags、作用域） |
| `memory_search` | 检索：metadata 过滤 + FTS/向量混合，支持跨 agent 检索（P1 的基础） |
| `memory_get` / `memory_update` / `memory_delete` | 按 id 操作；delete 是软删（记事件） |
| `summary_trigger` | 对某 session：抽事实（→semantic 层）+ 生成摘要（→summary 层） |
| `summary_get` | 取某 agent / 某 session 的最新摘要 |

**总结管道设计**（异步，不阻塞 agent）：
1. Agent 每轮对话把消息写入 `messages`（或 session 结束时批量提交）
2. 后台 worker 攒够 N 条或收到 `summary_trigger` 时调 LLM：
   - **事实抽取**：prompt 要求输出「关于用户/任务/决策的持久事实」列表 → 逐条写入 semantic 层，与已有记忆做去重/取代（更新 `valid_to`）
   - **滚动摘要**：新对话片段 + 旧摘要 → 新摘要，写入 summary 层
3. Agent 新 session 启动时调 `summary_get` + `memory_search` 注入上下文

## 六、P1：记忆迁移 / Handoff 协议

**payload schema**（基于 JetBrains Research 结论：结构化四字段优于原始历史）：

```json
{
  "task_id": "task_001",
  "from_agent": "research_agent",
  "intent": "调研 X 并产出对比报告",
  "changes_made": ["已抓取 5 个数据源", "已完成 Mem0/Zep 对比表"],
  "decisions_taken": [{"decision": "排除 CrewAI", "reason": "pickle 序列化脆弱"}],
  "open_questions": ["Zep 社区版是否还可自托管"],
  "memory_refs": ["mem_abc", "mem_def"],
  "instructions": "在此基础上完成 Zep 章节",
  "ttl_seconds": 86400
}
```

**交接闭环（三个 API）**：
1. `handoff_create` → 生成 package，`memory_refs` 指向的记忆**复制引用**到共享作用域（目标 agent 可读）
2. `handoff_claim(task_id)` → 接收方拉取 payload + 记忆；**前置校验**：schema 完整性 + 每条 ref 记忆的 `valid_to` 未过期
3. `handoff_ack(handoff_id, accepted, reason)` → 确认继承；超时未 claim 自动 expired，from_agent 可查询状态决定是否重派

**防坑机制**（来自调研的四大失败模式）：schema 校验防上下文丢失；记录 handoff 链防死循环（同 task 链长上限）；ack/超时防任务孤儿；接收方前置校验防错误静默传播。

## 七、实施步骤

| 步骤 | 内容 | 状态 |
|---|---|---|
| S1 | FastAPI 骨架 + SQLite + memories/messages 表 + 5 个记忆 API | ✅ 完成 |
| S2 | 总结管道：LLM 抽事实 + 滚动摘要（fake/OpenAI 兼容双 provider） | ✅ 完成（端到端可跑） |
| S3 | 检索增强：FTS5 trigram（中文）+ 向量余弦 + RRF 融合 | ✅ 完成 |
| S4 | handoff_packages 表 + 三个 handoff API + 校验/超时逻辑 | ⬜ 待做（P1） |
| S5 | 客户端 SDK（薄封装）+ 一个真实 agent 接入演示 | ⬜ 待做 |
| S6（P2，最后） | 存储抽象层换远程实现 + events.log 同步 | ⬜ 最后做 |

**S1–S3 已验证**：冒烟测试 21/21 通过（`python scripts/smoke_test.py`），真实 HTTP 服务 curl 验证通过。测试/演示可用内置 `MEMORY_LLM_PROVIDER=fake`（规则模型，零成本）；接真实模型设 `MEMORY_LLM_BASE_URL/API_KEY/MODEL`。
