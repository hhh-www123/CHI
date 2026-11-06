# Related Work 检索协议（冻结前）

## 研究范围

检索覆盖五条线索：Generative/Agentic UI、长程多轮代理评测、结构化/约束生成、版本与可逆交互、适当依赖和 mixed-initiative control。旅游推荐论文只在涉及持续编辑或事实性风险时纳入，不把一般推荐系统作为主体。

## 数据库

- ACM Digital Library；
- IEEE Xplore；
- ACL Anthology；
- arXiv（仅作预印本补充，记录后续正式发表版本）；
- Google Scholar/Semantic Scholar 用于前向与后向引用追踪。
- OpenAlex 只用于补充发现与元数据交叉核验，不替代上述主数据库。

## 核心查询

每组分别与 `user interface OR interactive system` 组合：

1. `generative UI OR agentic UI OR malleable interface`；
2. `long-horizon interaction OR multi-turn agent evaluation OR instruction retention`；
3. `structured generation OR constrained generation OR JSON patch`；
4. `version history OR undo OR provenance OR reversible interaction`；
5. `appropriate reliance OR mixed initiative OR human-AI control OR error recovery`。

检索日志记录数据库、完整查询、日期、结果数和导出文件。论文筛选截止日期在提交前冻结。

2026-11-03 已完成一轮 OpenAlex 探索检索：5 个查询各筛前 8 条，完整查询与结果数见 `literature-query-log.csv`；种子与直接相关条目已写入 `literature-log.csv`。这只是补充发现，不满足“两名研究者筛选主数据库”的系统检索完成条件。

## 纳入与排除

纳入：同行评议 HCI/AI 论文、直接相关高质量预印本、包含方法或实证结果的系统论文。排除：只有产品宣传、没有可核查方法、仅讨论静态一次生成、与交互状态保持无关的纯模型记忆论文。

两名研究者应对至少 20% 标题/摘要独立筛选并报告一致性。全文排除原因固定为：主题不符、无交互、无长期/更新维度、无方法细节、重复版本。

## 提取字段

使用 `literature-log.csv` 记录：研究对象、领域、交互长度、状态表示、是否有授权边界、版本/恢复、评价指标、自动实验、用户研究、样本量、开放资产、关键局限和本项目区别。

## 当前种子集合

`docs/evaluation.md` 中的 MultiChallenge、LongMemEval、τ-bench、MT-Eval、Lost in the Middle、Maru、Generative and Malleable User Interfaces、Guidelines for Human-AI Interaction 和 Appropriate Reliance 仅作为种子，不能视为系统检索已经完成。
