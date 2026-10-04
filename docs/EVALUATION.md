# 真实 agent 接手效果实验方案

目前**没有**真实 Agent A → Agent B 的效果结果。`demo_handoff.py` 只证明关键流程可运行。
下面的协议用于收集可比较证据，而不是把脚本成功率当作产品收益。

## 三种接手条件

在同一批真实、小而独立的编程任务上，各准备一个起点相同的独立工作区副本、相同的代码版本和测试。
随机分配条件；每个试验用新会话，统一模型版本、客户端版本、任务描述、权限、工具可用性和截止时间。

| `condition` | 接收方可见信息 |
|---|---|
| `repository_only` | 仓库、用户原始目标和可运行测试 |
| `progress_note` | 上述内容加一份普通进度说明，内容与交接包等价但非结构化 |
| `handoff` | 上述内容加结构化交接包及 CLI/MCP 工具；记录核验成本 |

不能让前一组的工作区修改、对话或日志泄漏给后续组。发送方的提示词与交接内容按同一模板生成，
并人工检查三组拥有相同关键事实。尽量用不同任务顺序抵消熟悉任务带来的学习效应。

## 记录方法

每次试验复制 [记录模板](../examples/evaluation-run.json) 到单独 JSON 文件并填写真实值。
`trial_id` 唯一；记录任务 ID、精确模型、客户端版本、起始 Git HEAD、完成/失败/超时、总秒数、
重复检查次数与可核验的证据路径。输入/输出 token 不可得时填 `null`，不要估算为零；
完成情况由预先写好的验收测试与人工 review 一起判定。

```bash
python scripts/summarize_evaluation.py trials/*.json
```

汇总脚本检查字段与数值、拒绝 NaN/Infinity、给每组显示样本数、完成数、时间中位数、
重复检查中位数以及有 token 数据的样本数/中位数。它是描述统计，不做因果推断。
报告应同时展示任务级失败案例、遗漏信息、交接创建成本和实际接手额外操作；
不要只展示完成案例或声称未经验证的显著提升。

## 自动准备独立试验目录

```bash
python scripts/demo_handoff.py --prepare-only --output reports/local/eval-fixture
python scripts/prepare_evaluation.py --fixture reports/local/eval-fixture --output reports/local/eval-trials --repeats 2
```

每个 fixture 包含 `workspace/`、`payload.json`、`progress.md`。可以替换为真实任务中断时的材料。
准备器建立相同文件内容和 Git 基线的独立副本，打乱三组执行顺序；每个交接组使用独立数据库和 v2 包。
输出目录必须不存在。不会复制 fixture 的 Git 历史、符号链接或常见凭据路径。
修改任一试验目录不会影响其他试验；记录 `manifest.json` 中的初始文件摘要用于确认起点。

每个目录中提供 `receiver-prompt.md` 和 `record.json`。用一个无先前历史的新 Agent 会话执行一项试验，
不要让接收者访问其他试验或种子目录；这由实验执行者控制，目录隔离本身不是操作系统权限隔离。
所有条件应使用相同模型与权限；普通说明组与结构化包组的关键事实需要人工核对为等价。

准备器的 `uses_llm=false`、`completed_trials=0`，记录初始状态为 `not_run`，汇总脚本会拒绝未运行记录。
脚本生成与脚本演示均不能算作真实 Agent 成功试验。
建议第一批用 5 个不同任务，每组重复 2 次，共 30 次；先分析失败案例，不做小样本显著性宣称。

## 发送成本与总成本

`elapsed_seconds`、`input_tokens`、`output_tokens` 记录接收阶段。
新增 `sender_elapsed_seconds`、`sender_input_tokens`、`sender_output_tokens` 记录对应条件下准备交接材料的增量成本。
无法测得时为 null。汇总同时显示接收阶段指标和双方合计指标；合计只使用双方数据完整的记录，报告其样本数，
缺失值不会当作零。原有 `median_total_tokens` 指接收阶段输入输出之和；双方合计字段为 `median_end_to_end_tokens`。
