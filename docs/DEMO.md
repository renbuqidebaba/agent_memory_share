# 复现一次编程任务交接

已安装项目后，从仓库根目录执行：

```bash
python scripts/demo_handoff.py --output demo-output
```

目标目录必须不存在。脚本只在该目录中建立分页函数与测试的 Git 仓库，并用同目录的 SQLite
数据库运行两个角色。发送方先修复不完整页的取整问题，测试仍因输入边界失败；它记录失败输出、
源码、测试文件和 Git HEAD，生成交接包与 `handoff.md`。接收方领取，核验文件与版本，接受后
补上无效输入检查，再运行测试，成功后标记完成。

查看：

- `demo-output/handoff.md`：人可读目标、完成标准、决策、下一步和失败验证。
- `demo-output/workspace/sender-tests.log`：接手前真实失败测试。
- `demo-output/receiver-tests.log`：修复后真实测试输出。
- `demo-output/result.json`：核验结果与最终状态。

此演示由确定性脚本分别扮演 A、B，没有启动 LLM agent，不能用来证明效率提升。

如需让真实 agent 自己接手，执行 `python scripts/demo_handoff.py --output <新目录> --prepare-only`。
把生成的工作区和任务 ID 交给新的 agent 会话，指示其按 [接收模板](../examples/agent-instructions.md)
查包、领取、核验、复验并补修。不要给该会话预期答案 `FIXED`；该答案仅存在演示脚本中，
因此做严格盲测时应换用独立准备任务与实验环境，见 [效果实验](EVALUATION.md)。
