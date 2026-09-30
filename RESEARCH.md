# 多 Agent 记忆管理系统 — 需求拆解与竞品调研

> 调研时间：2026-09-30 | 所有结论附来源

## 一、需求拆解

| 需求 | 本质问题 | 对应业界概念 |
|---|---|---|
| 1. 管理不同 agent 的对话记忆 + 总结 | 记忆分层（原始对话 → 提炼事实 → 摘要） | Memory Layer / Memory Extraction |
| 2. 跨计算机共享一份记忆 | 记忆与运行环境解耦，服务端集中存储 | Remote Memory / Cloud Memory Server |
| 3. 记忆迁移：agent 间交接任务上下文 | 结构化任务状态传递 + 记忆作用域共享 | Handoff / Context Transfer |

关键洞察：**这三个需求在业界是分开解决的**——Mem0/Zep 解决 1，mem9/Redis AMS 解决 2，OpenAI SDK/LangGraph/A2A 解决 3。把三者统一到一个系统里，正是本项目的差异化空间。

## 二、记忆管理层（需求 1）竞品对比

| 项目 | 架构 | Stars(2026.7) | 可自托管 | 特点 | 对本项目的借鉴 |
|---|---|---|---|---|---|
| **Mem0** | 向量 + KV + 实体链接，ADD-only 抽取管道 | ~61K | 是（Apache 2.0） | 框架无关；user/session/agent 三级作用域 | **作用域设计**（agent_id/run_id/user_id）直接可抄；注意 v2.0 起移除外部图数据库驱动，基准数据复现存疑 |
| **Zep / Graphiti** | 双时态知识图谱（valid time + transaction time） | ~28K | Graphiti 是；Zep 社区版已弃 | 事实带生效时间窗，能回答「两周前是什么状态」 | **时态模型**——记忆迁移时需要知道「这条记忆在交接时是否仍然有效」 |
| **Letta (MemGPT)** | OS 式三层：Core(常驻上下文)/Recall/Archival | ~24K | 是 | agent 用工具调用自己编辑记忆；sleep-time 后台整理 | 「记忆由 agent 自主管理」的理念；但它是完整 runtime，锁死重 |
| **Cognee** | ECL 管道：文档 → 知识图谱 | ~12K | 是 | 擅长从非结构化文档建 KG | 偏「机构知识」，对对话记忆场景过重 |
| **LangMem** | 多种记忆类型，绑 LangChain | 随 LC | 是 | LangChain 生态内即插即用 | 若用 LangGraph 栈可直接复用 |
| **Hindsight** | 多策略 + Reflect | ~4K | 是（MIT） | LongMemEval 91.4%，最高 | 基准领先但社区小，可跟踪 |

来源：mnemoverse.com Q3 2026 对比、developersdigest.tech、baeseokjae.github.io（均为 2026 年数据；注意厂商基准数字互相打架，Mem0 的 LongMemEval 在不同 harness 下从 49% 到 94% 都有，不可直接采信）。

## 三、跨设备共享（需求 2）— 与本案最对标的两个项目

### 3.1 mem9/mnemos（强相关，必须精读）
- GitHub: `mem9-ai/mem9`，Apache-2.0，官网 mem9.ai
- 架构：各平台 agent（Claude Code / OpenCode / OpenClaw）通过**无状态插件** → `mnemo-server` → TiDB Cloud（免费 25GiB）
- **解决的痛点与本案几乎完全重合**：session 结束失忆、agent 间信息孤岛、记忆绑本机换电脑就丢、不能团队共享
- 统一 5 个工具 API：`memory_store / memory_search / memory_get / memory_update / memory_delete`
- TiDB 原生 VECTOR 类型 + `EMBED_TEXT` 自动嵌入 → 不需要额外向量库和 OpenAI key
- 来源：temp.jaylab.io 深度报告（2026-03）

### 3.2 Redis Agent Memory Server
- 开源，**MCP over HTTP**——这是关键差异：任何支持 MCP 的客户端（Claude Code、Cursor、Codex、Gemini CLI）都能直连同一个记忆库
- 两层：working memory（session 级，自动提升为长期）+ long-term memory（语义检索）
- 后台 worker 用小模型做主题抽取/NER/记忆合并，可用 Ollama 本地模型，零 API 成本
- 来源：cloudrumble.net 部署实战（2026-02）

**结论：需求 2 的成熟解法 = 「记忆做成独立 MCP/HTTP 服务，agent 端只做无状态客户端」。不需要重复造轮子，但这两个项目都不做「迁移/交接」。**

## 四、记忆迁移 / Handoff（需求 3）调研

### 4.1 工业界实现

| 框架 | 交接机制 | 上下文传递方式 | 局限 |
|---|---|---|---|
| OpenAI Agents SDK | `handoff()` 作为 tool call | 默认传完整对话历史，`input_filter` 可裁剪 | 框架私有，跨 run 需自管持久化 |
| LangGraph | `Command(goto=...)` + typed state | 类型化状态对象，每 superstep 落 checkpoint（Postgres/SQLite） | checkpoint 体积膨胀需剪枝 |
| AutoGen | save_state/load_state | JSON 序列化消息线程 | 无原生崩溃恢复 |
| CrewAI | 任务链委托 | 任务对象序列化（pickle） | pickle 跨版本脆弱 |
| **Google A2A** | Agent Card + JSON-RPC 2.0 任务生命周期 | 标准化 task 状态机（submitted→working→completed） | 管「通信」不管「记忆内容」 |
| Anthropic 长任务 harness | 文件系统即状态 | `claude-progress.txt` + JSON checklist + git commit 作为检查点 | 朴素但极可靠 |

来源：peppereffect.com、cowork.ink、zylos.ai（2026-08）、blckalpaca.at。

### 4.2 交接内容的关键研究结论（重要）

JetBrains Research (2025)：**原始对话历史是错误的交接单元**。结构化状态（四字段：intent / changes made / decisions taken / open questions）在上下文管理评测中得分 4.04/5，显著优于原始历史。设计原则：「serialize what matters, not everything that happened」。

Handoff payload 参考 schema（reputagent.com）：
```json
{
  "from_agent": "...", "to_agent": "...", "task_id": "...",
  "context": {"findings": [], "sources_consulted": [], "open_questions": []},
  "instructions": "..."
}
```

四种上下文传递策略：全量（安全但爆 token）/ 摘要（高效但丢细节）/ **结构化字段（推荐）** / 引用式（传 ID，接收方自取，适合大上下文）。

四大失败模式：上下文丢失（→ schema 校验）、死循环（→ 最大交接深度）、任务孤儿（→ 接收方 ack + 超时升级）、错误静默传播（→ 接收方前置校验）。

### 4.3 学术参考（与科研方向契合）

- **A-MEM**（arXiv 2502.12110）：Zettelkasten 式 agentic memory，新记忆自动生成上下文描述/关键词/标签，动态建链 + 触发旧记忆演进。开源：`agiresearch/A-mem`
- **MemGPT/Letta**：OS 虚拟内存隐喻，agent 自主分页
- **MemoryOS**：STM/MTM/LPM 三级 + 分层检索
- **MemOS**：MemCube（内容 + 溯源 + 版本元数据）——「记忆打包成可迁移单元」的概念与本案的迁移需求直接对应
- **HiAgent**（ACL 2025）：以 subgoal 为记忆分块单元的层次化工作记忆

## 五、差距分析：本案的差异化空间

| 能力 | 现有项目覆盖 | 缺口 |
|---|---|---|
| 记忆抽取/总结/检索 | Mem0、Zep 已很成熟 | 无需重造，可嵌入 |
| 跨设备共享 | mem9、Redis AMS 已解决 | 无需重造 |
| **agent 间记忆迁移 + 任务上下文继承** | 各框架私有 handoff，互不通用；A2A 只管通信不管记忆内容 | **核心机会：定义一个跨框架的「记忆交接包」格式** |
| 迁移时的记忆有效性（时态） | 仅 Zep 有双时态 | 交接包应带 valid-time 元数据 |
| 迁移的可靠性（ack/校验/回滚） | 仅设计模式层面讨论 | 可做成交接协议的一等公民 |

## 六、初步架构建议

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Agent A    │  │  Agent B    │  │  Agent C    │   ← 各 agent 无状态客户端
│ (Claude Code)│  │ (自研框架)  │  │  (其他)     │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │  MCP / HTTP (JSON-RPC)  │
       └──────────┬───────────────┘
        ┌─────────▼─────────┐
        │  Memory Server    │
        │ ├─ 记忆分层存储    │  working(短期) / episodic(情节) / semantic(语义)
        │ ├─ 总结提炼管道    │  对话 → 事实 → 摘要（LLM 后台异步）
        │ ├─ 作用域隔离      │  agent_id / user_id / session_id / shared
        │ ├─ 交接包管理      │  handoff_package: 创建/领取/ack/校验/回滚
        │ └─ 时态元数据      │  valid_from / valid_to / 版本
        └─────────┬─────────┘
        ┌─────────▼─────────┐
        │  存储: SQLite/PG   │  向量 + 关系一体（PG+pgvector 或 TiDB）
        │  云端部署          │  → 跨设备天然共享
        └───────────────────┘
```

**交接流程（需求 3 的核心闭环）**：
1. Agent A 调 `handoff_create(task_id, context_schema, memory_refs[])` → 生成结构化交接包（引用式 + 结构化字段，不传原始历史）
2. Agent B 调 `handoff_claim(task_id)` → 拉取交接包 + 按 memory_refs 领取相关记忆副本/引用
3. Agent B 前置校验（schema + 记忆时效）→ ack；超时未 ack 则升级/回滚
4. B 执行中产生的新记忆写回同一 task 作用域，可再交接

## 七、落地路线建议

- **MVP（1-2 周）**：FastAPI + SQLite/pgvector，实现 mem9 式 5 个记忆 API + 作用域；部署到云 → 解决需求 1、2
- **V2**：加 handoff 三 API（create/claim/ack）+ JetBrains 四字段 schema → 解决需求 3
- **V3（科研向）**：引入 A-MEM 式记忆演进 + Zep 式时态元数据；这可以作为论文点（与 KG + RL 方向结合：用 RL 学习「哪些记忆该进入交接包」——现有工作没人做）
