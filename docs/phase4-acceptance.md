# Phase 4 验收与阶段停止报告

日期：2026-10-31。目录travel-genui_1，版本0.6.5-phase4。完成协议子集适配、流式进度、硬约束后规则排序和观测，不重写原网页，不绕过PatchGuard/候选提交，不开展正式实验。

## 1. 前置门禁与官方核实

已读根技术规范、Phase0审计、Phase1交付、Phase2/3验收、最新范围扩展实现及其真实测试记录；业务事实源、稳定ID、选区/锁、完整Diff、候选缓存、SQLite CAS和持久版本沿用。

实际运行`pnpm build`和隔离后端全套：构建通过，原301项通过36.192s，见[门禁构建](verification/phase4-gate-build.log)、[门禁后端](verification/phase4-gate-backend.log)。没有核心保真阻断后才开发本轮功能。

核查官方A2UI当前v0.9.1、Renderer矩阵、@a2ui/react main源码0.14.0及其Zod3 peer，与本地React19.3.1/Zod4.1.12对比；AG-UI官方事件文档及core main版本1.0.3、protocolVersion1.0也已核对。未安装官方SDK，未编造库方法。完整来源、结构、版本与缺失能力见[protocol-integration.md](protocol-integration.md)。

## 2. 实际完成能力

- 自定义只读A2UI v0.9.1 envelope/Catalog子集：TripCard、ActivityCard通过严格Schema映射到原StopCardContent，继续使用原数据绑定和卡片/时间轴样式。文本由React转义，候选不接受URL/HTML/JS/CSS/事件属性，不能产生外链或地图副作用。
- 原候选POST接口按Accept协商JSON或SSE；原JSON API保留，正式页面采用SSE。RUN_STARTED、CUSTOM、RUN_FINISHED、RUN_ERROR遵循核实的事件结构。范围检查、生成、补充重试、验证和逐候选到达均来自实际阶段回调，不使用伪造进度百分比。
- 客户端流式解析处理UTF-8分块、LF/CRLF、注释，限制事件/总字节与事件数，严格验证生命周期及结束；断流、未知事件、STATE_DELTA、非法组件/绑定均丢弃草稿。支持previewOnly状态元数据的安全忽略；不提供事件写正式状态功能。
- Schema、路径白名单、锁、时间/预算不变量、真实叶Diff和完整原业务Diff全部通过后才评分。服务端计算五项[0,1]规则代理、解释依据、变更成本与推荐次序，不信模型自评分。权重存JSON配置并进入源码版本指纹，候选采用后评分存入同一SQLite版本审计。
- 结合最终状态、真实地点和时点差去重，并提供变更路径Jaccard/最终状态相等说明。LLM合法不同方案不足3个时最多在同快照同范围补充一次，提案精确去重后再次硬校验；不以非法重复候选凑数。
- 前端显示生成/失败/重试/停止等待、候选模式、合法性、差异、规则分与推荐；只有采用后，commit重新从服务器候选读取Patch、解析当前权限并CAS写入。流式/A2UI事件不能改变锁或绕过409。
- JSON结构日志含生成毫秒、合格率、提案数、重试次数和提交耗时，失败只记录安全状态码；不记录密钥、提示全文或上游响应。实际[观测样本](verification/phase4-observation-sample.json)取自隔离服务运行日志。

## 3. 最终命令、结果与日志

| 实际命令 | 最终真实结果 | 证据 |
| --- | --- | --- |
| `pnpm build` | TS+Vite通过，1662模块，Vite2.42s | [build](verification/phase4-build.log) |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase4-tests.ps1` | **308项通过，36.652s，无跳过**，原301+新增7 | [backend](verification/phase4-backend.log) |
| `node tests/trip-schema.cjs` | 12项通过 | [Trip Schema](verification/phase4-trip-schema.log) |
| `node tests/phase2-schema.cjs` | 38项通过；适配模块加载，原断言保留 | [候选Schema](verification/phase4-client-schema.log) |
| `node tests/phase4-protocol.cjs` | **19项通过**，非法消息与断流检查 | [协议客户端](verification/phase4-client-protocol.log) |
| `node tests/phase4-ui.cjs` | **9组通过**，无pageerror/外网请求，8015独立UUID库 | [浏览器日志](verification/phase4-browser.log)、[JSON](verification/phase4-browser.json) |
| `node tests/run-phase4-regressions.cjs` | **P1原12组＋P2原9组通过**，自管8013空Key/TTL30 | [继承汇总](verification/phase4-regressions.log)、[P1](verification/phase4-p1-browser.json)、[P2](verification/phase4-p2-browser.json) |
| `$env:UI_ARTIFACT_PREFIX='phase4-regression'; node tests/phase3-ui.cjs` | **多轮7组通过**，10主序列＋1次冲突准备；真实重启通过 | [多轮日志](verification/phase4-multiround.log)、[JSON](verification/phase4-regression-browser.json) |
| `$env:TRAVEL_LAUNCH_NO_BROWSER='1'; & '.\启动旅序.cmd'` | 8011启动最新版，前后端修订匹配 | [启动](verification/phase4-launcher.log)、[当前修订](verification/phase4-live-revision.json) |

最终资源index-ChitqQ1O.js / index-Bj6lERZS.css；无新增npm/Python依赖、无锁文件变更。协议核实与浏览器注入是开发验证，不是正式比较实验；没有真实付费模型或高德请求。既有本地HTTP fixture仍验证真实httpx兼容调用，不将Mock表述为模型联调成功。

新增后端7项覆盖：真实SSE生命周期/阶段与预览不写库、缺Key终止安全错误、恶意模型补丁无评分无Surface、评分归一化/确定性/依据、最终状态重复过滤、JSON与流式共用正式提交及评分持久化、同范围仅一次LLM补充。

客户端19项覆盖：合法Catalog、未知HTML组件、html/href/javascript URL/onClick等属性、越界绑定、循环根、surfaceId/版本错误、伪造STATE_DELTA/STATE_SNAPSHOT、未知CUSTOM、错误runId、重复开始、缺结束、UTF-8逐字节分块、错误JSON。正式数据不在事件状态机的可写引用中；E2E另核对API与SQLite逐字节不变。

## 4. 端到端结果

- Phase4 real progress, ranking, declarative preview, disconnect/illegal STATE_DELTA/component rejection, retry and cancel are isolated：通过。
- Unlocked first-day morning supports three real mock candidates; preview is isolated and only B is saved：通过。
- Valid time changes cross periods with stable IDs; moving into a locked target period is rejected：通过。
- Generalized dates preserve exact selected scope and reject direct writes to derived metadata：通过。
- Third-day evening supports genuine candidate edits without changing other days：通过。
- Manual Patch replaces a real catalog place; its title is server-derived and stable IDs are preserved：通过。
- Natural-language place replacement produces three distinct real POIs; candidate B alone enters persistent history：通过。
- Unknown POIs, object locks and stale versions remain enforced; reload preserves generalized edits and constraints：通过。
- Original map fallback, navigation and persistent history remain usable on desktop and mobile：通过。

继承多轮：成功提交11次（前10次主序列），直接未授权路径0条；锁/约束保持，真实进程重启29412→17056，正式数据/版本/快照相同。恢复仍创建新版本，旧候选409；流结束/重启都不自动采用候选。

原地图、按钮、导航、独立滚动、真实景点备选、历史、刷新与手机布局保留。地图只测试无Key降级和安全外链，不声称真实瓦片或付费路线已联调。多轮几何/截图继续区分间接布局与直接业务变化，限制与前轮报告一致，不能以像素通过证明全部语义正确。

## 5. 调试失败与修复（保留记录）

- 首次构建因TS联合类型未充分收窄出现4处name/value错误；显式要求CUSTOM分支后通过，未使用any绕过类型。见[首次构建](verification/phase4-build-first.log)。
- 首次后端302项发现新测试使用相对导入不适配unittest discover，以及重试后重复提案导致旧重复计数断言失败。改绝对导入和精确提案去重，未放宽硬校验或旧去重断言；最终308项通过。见[初次后端](verification/phase4-backend-initial.log)。
- Chromium CDP不稳定提供SSE response.text()，导致初次Phase4/多轮浏览器测试读取失败。测试改为在浏览器中只读clone同一次fetch，不增加请求，不改产品正式状态断言。P2还需区别APIResponse与页面Response，以及HTTP200中RUN_ERROR的503语义码，测试助手显式检查该码；不是把错误流当成功。相关[Phase4首次](verification/phase4-browser-first.log)、[多轮首次](verification/phase4-multiround-first.log)、[P2首次](verification/phase4-p2-first.log)、[P2第二次](verification/phase4-p2-second.log)均保留。
- 最终P1/P2/Phase4/多轮全部通过。历史依赖运行任意模型JS的旧自由代码脚本与当前安全规范冲突，不作为可用验收脚本；源码/记录保留。当前全部Python单元与现用浏览器回归均已执行。

## 6. 使用与截图

启动本目录启动旅序.cmd或访问http://127.0.0.1:8011/，确认0.6.5-phase4。编辑模式选中未锁定日期/时段/卡片→输入需求→选Mock或已配置LLM→生成→观察真实阶段与候选到达→比较规则推荐和真实预览→采用。失败后可重试，停止等待/取消不写正式数据。原跨时段和真实目录景点功能保留。

- [三候选与规则推荐](screenshots/phase4/first-morning-candidates.png)
- [第三天晚上预览](screenshots/phase4/third-evening-candidates.png)
- [真实景点方案](screenshots/phase4/real-place-candidates.png)
- [手机历史](screenshots/phase4/mobile-history.png)
- [十轮后重启历史](screenshots/phase4-regression/history-after-restart.png)

截图已打开目视检查；没有录屏。README、启动旅序.md、启动窗口版本及架构入口已更新。原项目文件与副本.env未改变，文件校验见机器清单。

## 7. 所有新增/修改文件

新增：

```text
backend/candidate_ranking.py
backend/protocol_adapter.py
backend/ranking_weights.json
backend/stream_adapter.py
backend/test_protocol.py
docs/phase4-acceptance.md
docs/protocol-integration.md
scripts/run-phase4-tests.ps1
src/features/agentic-editor/protocol.ts
tests/phase4-protocol.cjs
tests/phase4-ui.cjs
tests/preview-response.cjs
tests/run-phase4-regressions.cjs
```

修改：

```text
README.md
backend/app.py
backend/candidate_api.py
backend/revision.py
backend/test_candidate_api.py
backend/versioning.py
docs/architecture.md
package.json
src/api.ts
src/features/agentic-editor/CandidatePanel.tsx
src/features/agentic-editor/candidateSchema.ts
src/features/agentic-editor/candidateTypes.ts
src/styles.css
tests/edit-scope-ui.cjs
tests/phase1-ui.cjs
tests/phase2-schema.cjs
tests/phase2-ui.cjs
tests/phase3-ui.cjs
启动旅序.cmd
启动旅序.md
```

证据为docs/verification/phase4-*与docs/screenshots/phase4*/；完整清单及原项目/.env核验见[phase4-file-manifest.json](verification/phase4-file-manifest.json)。没有删除原源码、资源、旧报告或用户数据。

## 8. 协议缺失能力与已知风险

1. 是自建受限A2UI/AG-UI消息适配，不是官方SDK全兼容；只读TripCard/ActivityCard，未实现协议BudgetCard/MapPanel、完整Basic Catalog、updateDataModel/deleteSurface、action/双向绑定、主题/函数、MCP/A2A协商或官方一致性认证。原地图组件不受影响。
2. AG-UI仅生命周期＋CUSTOM阶段；不是官方RunAgentInput服务。STATE_DELTA拒绝，STATE_SNAPSHOT草稿元数据不应用。无续流、自动重放、模型token级流、工具/推理事件。HTTP200内错误用RUN_ERROR语义码明确提示。
3. 用户取消立即停止前端加载并废弃迟到结果，但同步上游调用可能持续至返回或超时；最多4个生成槽，缓存最多500条及TTL。未采用候选不写正式库，继续单worker。
4. 五项评分全部是规则代理，结构功能/授权稳定性来自已定义检查，不能证明全部交互或视觉恒定；语义匹配与可用性未进行用户研究，效率没有真实路线优化。权重固定可配置但未实验调参。
5. 前端候选最终批次完整校验后才可采用；到达事件只展示验证进度，不边到达边写正式状态。预览最多32个活动组件，每流最多100事件/5MB；此为受限实现边界。
6. 原LLM、地图配置保留；本轮未真实服务商联调、容器构建或公开发布。生成/提交日志只提供开发观测，不冒充正式实验结果。

**Phase 4已完成后停止，等待用户决定是否开展正式实验。**
