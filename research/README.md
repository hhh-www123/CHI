# 论文研究资产

这里存放论文协议与正式实验入口；`docs/verification/` 仍是工程验收记录，两者不能混作论文数据。

## 当前状态

- 论文协议、假设、主要终点与结论边界：草案；
- 6 类任务 × 4 长度 = 24 个核心条件：Schema 有效，尚未人工复核和冻结；
- A–D 方法与 Prompt：机器可读草案；执行适配器、逐 run 原子落盘、预算闸门和安全续跑已完成；
- 正式 manifest：双模型设计下可生成 960 个运行槽（每模型480），但会列出阻塞项；
- 任务模板级 Bootstrap、精确配对 sign-flip 与 Holm 校正：合成数据测试通过；跨长度描述分析成组保留四个长度和 trial，注册主终点敏感性分析按模型限定20轮，最终论文仍需冻结并运行预注册的混合效应分析；
- 混合效应分析：第 11 项签字后，规范与 R 4.6.2/四个精确包版本已冻结为 `frozen` / `frozen-tested`；960-episode 合成合同和哈希绑定证据有效，尚未运行任何正式数据；
- 研究决策：第 1–11 项推荐方案均已接受；第 7 项保留双模型设计，但精确模型身份、价格和预算上限尚未填写；
- 人工标注：HMAC 盲化导出、私有映射、双标/仲裁和评分合并链路已实现，尚无正式标签；C–D 用户研究尚无参与者或数据。

## 文件

- `protocol.md`：论文定位、RQ/H、主要终点、统计、排除和冻结规则；
- `annotation-rubric.md`：语义目标、旧目标泄漏和候选 NDCG 的盲法标注规则；
- `user-study-protocol.md`：C–D 被试内研究、受控错误和伦理检查；
- `user-study-instruments.md` / `consent-template.md`：主持、问卷和同意材料的冻结前模板；未获审核前不得招募；
- `paper-outline.md`：论文结构与每节必须提供的证据；
- `evidence-matrix.md`：每条潜在主张所需证据、当前状态及降级写法；
- `open-decisions.md`：会改变 A–D 公平性或论文范围、必须在冻结前签字确认的设计选择；
- `literature-search-protocol.md` / `literature-query-log.csv` / `literature-log.csv`：Related Work 的查询日志与逐篇提取记录；当前 OpenAlex 轮次仅作补充发现，主数据库双人筛选仍未完成；
- `related-work-matrix.md`：已提取种子的状态、授权、恢复与评价维度对比，以及可防守的窄差异主张；
- `../evaluation/tasks/`：任务 Schema、草案模板与确定性编译器；
- `task-review-template.csv`：24 个条件冻结前的双人任务复核记录模板；
- `prepare_task_review.py` / `task-review-guide.md`：展开全部目标、干扰轮与 oracle 的确定性未审批审阅包，以及独立双人复核流程；
- `../evaluation/prompts/v1/`：共享上下文和 A–D 方法差异 Prompt；
- `../evaluation/methods.json`：机器可读方法条件与公平性规则；
- `../evaluation/model_client.py`：不回退 Mock、只在内存持有密钥的真实模型单次调用客户端；
- `../evaluation/run_real_experiment.py`：只接受正式就绪 manifest 的逐 run runner；保存原始响应、token、延迟、费用与失败，并拒绝静默重跑中断调用；
- `../evaluation/prepare_blinded_annotations.py`：将完整正式 run 导出为不含方法、模型、策略、规则分和运行 ID 的语义/候选标注包，方法映射单独私存；
- `../evaluation/annotation_resolution.py`：核对标注覆盖、双标、仲裁、Cohen's κ 与 Krippendorff ordinal α；
- `../evaluation/assemble_scoring_dataset.py`：只在标签闸门通过后合并确定性状态指标和盲法人工标签，生成评分器输入；
- `../evaluation/annotations/`：盲法标签合同与仅作格式演示的双标样例；
- `../analysis/bootstrap.mjs`：按 task 而非单次 trial 重采样的区间分析。
- `../analysis/drift_survival.mjs`：按模型和长度分层的首次失真 Kaplan–Meier 曲线、模板聚类区间与配对差值。
- `../analysis/retention_curves.mjs`：目标年龄/约束年龄保持曲线、非等距 AUC、模板聚类区间与配对差值。
- `../analysis/verify_runtime.py` / `../analysis/runtime-verification.json`：重跑隔离 R 合成合同，并保存与当前统计资产哈希绑定的非正式验证证据。
- `readiness_audit.py`：区分工程、配置、人工复核、文献、统计运行时与伦理阻塞的机器可读论文就绪审计；不把文件存在误当成人工批准。
- `external-evidence-contracts.md`：标注 pilot、伦理决定和用户 pilot 的最小可验证摘要格式；不含伪造的已批准样例。

## 检查命令

```bash
.venv/bin/python evaluation/task_suite.py --check
.venv/bin/python evaluation/prompt_bundle.py
.venv/bin/python evaluation/prepare_experiment.py --output /tmp/travel-formal-manifest-draft.json
.venv/bin/python evaluation/run_real_experiment.py --manifest /tmp/travel-formal-manifest-draft.json --output-dir /tmp/must-not-run
.venv/bin/python evaluation/validate_annotations.py evaluation/annotations/example.json
.venv/bin/python evaluation/run_contract_pilot.py --output /tmp/travel-ad-contract-pilot.json
.venv/bin/python evaluation/run_contract_pilot.py --all-families --output /tmp/travel-ad-contract-pilot-all.json
.venv/bin/python evaluation/run_contract_pilot.py --all-conditions --output /tmp/travel-ad-contract-pilot-matrix.json
.venv/bin/python research/prepare_task_review.py --output /tmp/task-review-packet.json
node analysis/bootstrap.mjs evaluation/example-dataset.json /tmp/example-bootstrap.json --iterations 2000
.venv/bin/python analysis/verify_runtime.py --output /tmp/runtime-verification-review.json
.venv/bin/python research/readiness_audit.py --output /tmp/research-readiness.json
pnpm test:research
```

Windows 把 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

第三条命令当前应输出 `formalReady: false`，第四条必须拒绝且不能创建输出目录。只有任务、方法、Prompt、模型配置、分析规范及经过合成测试的精确 R/包版本全部冻结，工作树干净，真实模型身份、单价、保留政策、预算与凭据齐备后，才能加 `--formal`；否则准备脚本以状态码 2 拒绝且不创建正式 manifest。

manifest 的 `costPlan` 预先给出无重试计划调用数：当前双模型设计合计 8040 次真实调用（每模型 4020），最大分配输出 48,240,000 token。填写单价后会给出输出分配费用；输入费用依赖实际历史长度，runner 逐调用按保守上界守住总预算。它还登记最多 1080 个语义样本和 150 个候选批次，共 1230 个唯一盲法样本；语义/候选各至少 20% 双标时，最低为 1476 次样本分配，仲裁另计。若每个样本均双标且均需仲裁，双编码者模型下的规划上界为 3690 次分配。`costPlan` 不是报价，也不能替代供应商价格、额度与标注工时确认。

每个 A–D block 使用相同 fixture 所有的 trip/entity/component ID；方法顺序不是独立乱序，而是在每个 `模型 × 长度` 层内由随机种子轮换 Williams 序列。30 个 block 中每个方法在每个位置出现 7–8 次，降低服务时段、缓存或限流与方法条件共变的风险。

`run_contract_pilot.py` 使用确定性 fixture 贯通 A–D，只检查历史、Prompt、解析、可信 Setup 与状态执行契约；`--all-families` 覆盖六类5轮任务，`--all-conditions` 覆盖完整24条件、96个episode和864轮，包括 edit/clarify/reject/restore/update_constraint。输出固定标记 `fixture: true`、`formalExperiment: false`，不能比较方法质量。

正式冻结后，先将 manifest 写入忽略目录，再启动 runner：

```bash
.venv/bin/python evaluation/prepare_experiment.py --formal --output results/private/formal-manifest.json
.venv/bin/python evaluation/run_real_experiment.py --manifest results/private/formal-manifest.json
# 分批运行时可加 --max-runs N；下一批使用相同命令并加 --resume
```

默认结果目录为 `results/raw/<experimentId>/`。每次模型调用前先写预算保留记录，每个 run 完成后原子创建不可覆盖的结果文件。普通 `--resume` 跳过已有 run；若进程在调用中断，必须显式加 `--retry-interrupted`，旧尝试会归档且其保守预算占用不会被抹除。该参数是付费重试确认，不应用于绕过失败结果。

全部 run 完成后才能导出标注包。盲化密钥应为至少 32 字节的随机秘密，只放在环境变量中；公开包与私有映射必须分开保存：

```bash
ANNOTATION_BLINDING_KEY='由研究者安全生成并保存的随机值' \
  .venv/bin/python evaluation/prepare_blinded_annotations.py \
  --results results/raw/<experimentId> \
  --packet results/private/annotation-packet.json \
  --private-map results/private/annotation-map.json

.venv/bin/python evaluation/validate_annotations.py results/private/formal-annotations.json \
  --packet results/private/annotation-packet.json \
  --private-map results/private/annotation-map.json

.venv/bin/python evaluation/assemble_scoring_dataset.py \
  --results results/raw/<experimentId> \
  --packet results/private/annotation-packet.json \
  --private-map results/private/annotation-map.json \
  --annotations results/private/formal-annotations.json \
  --output results/private/scoring-dataset.json
```

评分合并不会把缺失标签当作成功；无效 JSON、模型错误和守卫拒绝按预注册规则保留为失败。长期目标只在注册的 probe 机会计分；combined/probe 和真实模型 setup 使用盲法人工语义标签，中间 distractor 的即时完成只使用明确标记的结构代理，不进入“人工语义完成率”。这样既避免把没有触发机会的偏好/版本目标误判为遗忘，也把约 8040 轮全人工标注降到课程可执行范围。候选人工 NDCG 同样限于上述关键轮；全部轮次仍计算自动合格率与唯一结果率。候选目标数已写入任务源：开放式编辑为 3，澄清、拒绝和约束工具轮为不适用。

## 正式研究数据规则

- 正式输出使用全新目录，不与 `/tmp`、Mock 或工程回归结果合并；
- manifest 不保存 API Key，只记录运行时是否存在凭据；
- 无效 JSON、超时、越权拒绝和零变化都保留为结果；
- 不允许看到结果后更改任务真值、主终点、权重或排除规则；
- Mock 与示例数据永远保持 `formalExperiment: false`。
