# 24 条件双人任务复核指南

先生成当前编译任务的确定性审阅包：

```bash
.venv/bin/python research/prepare_task_review.py --output /tmp/task-review-packet.json
```

两名审阅者使用同一 `benchmarkHash`，分别阅读每个条件的全部轮次、目标账本、候选数和 oracle，再独立向 `task-review-template.csv` 追加 24 行；每行的 `benchmark_hash` 必须复制审阅包中的完整 64 位值。`reviewer_id` 使用稳定的匿名研究者代码；`review_round` 从 1 开始。八个布尔字段必须逐项填写，不能只填总 `approved`。

若任一项不通过，填写固定 `issue_category`（`ambiguity`、`oracle`、`distractor`、`method_asymmetry`、`external_fact`、`severity` 或 `other`）和可执行说明。先修改任务源并重新编译；由于 `benchmarkHash` 会改变，旧复核不自动沿用。第二轮复核写新行，不覆盖第一轮。

两人完成前不得讨论具体判断。独立阶段结束后可对分歧进行会议记录，但 CSV 中保留原判断；最终只有同一任务获得两名不同 reviewer 的完整通过行，readiness audit 才计为通过。审阅包的 `status: unreviewed` 和 `packetHash` 只证明材料一致，不代表批准。
