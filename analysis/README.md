# 论文分析资产

`bootstrap.mjs` 对每种方法先在 task 内汇总 trial，再以 task 为重采样单位计算百分位 95% 区间，并对共享 task 的方法做配对差值。它用于数据质检、图表区间和稳健性分析，不替代 `research/protocol.md` 预先规定的混合效应主模型。

```bash
node analysis/bootstrap.mjs results/formal-dataset.json results/bootstrap-primary.json \
  --iterations 10000 --seed 20261103 --model-key primary --length 20
```

正式主终点稳健性分析必须为每个模型身份分别执行，并显式选择 `--length 20`。只有同时包含 A–D、每种方法至少六个独立任务且数据明确标记 `formalExperiment: true`、标注来源正式就绪时，输出才会把 `inferenceReady` 标为 true。六个模板只有 64 种符号分配，脚本会精确枚举而不是用随机近似；Holm 族仅包含注册的 D–A/B/C 三项 task-success 比较。省略长度仍可生成跨长度描述性区间，但不会被标记为正式主终点推断就绪。

## 混合效应主分析候选

`model-spec.json`、`prepare_mixed_effects.mjs` 和 `mixed_effects.R` 将统计计划具体化并已于 2026-11-06 冻结；这只是预注册分析合同，不是已经执行的论文分析：

- 主终点是 20 轮条件的严格 episode task success；
- 两个模型身份分别拟合和报告，不把它们伪装成模型总体的随机样本；
- 每个模型拟合 `success ~ method * interaction_length + execution_position + (1 | template_id) + (1 | block_id)` 的 Logistic 混合模型；
- 主比较仅为 20 轮的 D–A、D–B、D–C，按模型分别使用 Holm 校正；
- template 固定效应模型、模板级 sign-flip/Bootstrap 和模型异质性分析只是预注册敏感性分析；
- 技术失败保留为失败，缺失人工标签在组装阶段阻断，不做成功插补。

正式数据组装后先运行设计审计：

```bash
node analysis/prepare_mixed_effects.mjs results/formal-dataset.json results/mixed-effects-input
R_LIBS_USER="$PWD/.r-library" Rscript analysis/mixed_effects.R results/mixed-effects-input results/mixed-effects.json
```

准备器要求恰好 960 个 episode、240 个四方法配对 block、两个模型、六个模板、四种长度和每格五次 trial，并复核 Williams 执行位置平衡、哈希和正式标注来源。输出目录必须不存在，任何未冻结合同都会在写文件前被拒绝。

当前 `model-spec.json` 的分析状态是 `frozen`，运行时状态是 `frozen-tested`。2026-11-06 已用 R 4.6.2、lme4 2.1.0、emmeans 2.0.4、jsonlite 2.0.0 和 digest 0.6.39 完成隔离合成测试并锁定精确版本；两个模型身份的主模型与 template 固定效应敏感性模型均可估计且无收敛或奇异拟合警告。机器可读证据见 `runtime-verification.json`，它绑定模型规范、准备器、拟合脚本、夹具生成器与验证脚本的哈希；任一资产变化都会使 readiness 审计拒绝旧证据。正式准备命令仍会拒绝非正式、缺标注或来源哈希不匹配的数据。

R 运行时只能用隔离的合成合同夹具做首次验证：

```bash
node analysis/generate_mixed_effects_fixture.mjs /tmp/mixed-fixture.json
node analysis/prepare_mixed_effects.mjs /tmp/mixed-fixture.json /tmp/mixed-input --contract-test
R_LIBS_USER="$PWD/.r-library" Rscript analysis/mixed_effects.R /tmp/mixed-input /tmp/mixed-result.json --contract-test
.venv/bin/python analysis/verify_runtime.py --output /tmp/runtime-verification-review.json
```

最后一条命令会从头重跑夹具、设计审计和 R 拟合，并以不可覆盖方式生成供复核的证据。夹具固定标记 `syntheticContractTest:true`、`illustrative:true`、`formalExperiment:false`、`realModel:false`；准备清单和 R 输出也保持 `formalAnalysisReady:false`。普通正式模式拒绝该夹具，`--contract-test` 也拒绝任何未带完整合成标记或分析哈希不匹配的数据。

正式论文分析仍须报告：

- 效应量、区间、原始分母和失败样本；
- 模型假设与敏感性分析。

首次失真曲线按模型和交互长度分层，不能把 L1/L5 的设计截尾与 L20 混入同一风险集：

```bash
node analysis/drift_survival.mjs results/formal-dataset.json results/drift-primary-L20.json \
  --model-key primary --length 20 --iterations 10000 --seed 20261104
```

该脚本输出 Kaplan–Meier 点估计、失真事件/行政截尾分母、平均无失真轮数，以及按六个任务模板整组重采样的逐点 95% 区间；D–A/B/C 曲线差值保留方法配对。区间是逐点区间而非同时置信带，终点 sign-flip 只是次要敏感性分析。

目标与约束保持曲线同样按模型分别生成，并以模板整组保留四个长度和全部 trial：

```bash
node analysis/retention_curves.mjs results/formal-dataset.json results/retention-primary.json \
  --model-key primary --iterations 10000 --seed 20261104
```

输出包含每个目标年龄的权重分子/分母、逐点聚类区间、非等距梯形 AUC 及 D–A/B/C 配对曲线差值；goal-update 中明确标记 `retentionEligible:false` 的目标不会误入长期保持分母。

最终模型规范、脚本和 R 运行时必须在正式方法标签解盲前冻结。仓库当前没有正式数据，脚本不会生成或填充任何论文结果。
