# 技术规范文本摘录

来源说明：此 Markdown 由 Phase 1 留存的同名 DOCX 全文摘录复制而来，原始来源为同级技术规范目录。Phase 2 开始时仓库根目录没有同名 Markdown，因此补齐此入口；未虚构额外规范。本轮授权范围以用户明确要求的 Phase 2 为准。

AGENTIC UI / GENUI

三日游网页动态界面生成与目标保真

工程技术实施规范  ·  交付 Codex

多候选生成  /  可控局部编辑  /  多轮目标保真

版本 1.0    ·    2026 年 10 月 28 日

适用于已有三日游网页的增量开发与课程实验

文档类型：交付 Codex 的工程实施规格（Implementation Spec）版本：1.0（2026-10-28）项目现状：已有一个可运行的“三日游”网页；本方案是在现有项目上增量开发，不要求重做旅游规划系统。核心目标：每次自然语言修改产生多个候选界面；用户可指定修改范围并锁定其他区域；连续多轮编辑后未授权区域和既有约束不被悄悄改变。

0. 给 Codex 的最高优先级指令

1.  先检查仓库再修改：阅读 README、package.json、路由、现有三日游页面、组件、API、状态管理、测试和构建脚本；输出 docs/implementation-audit.md，记录实际框架、可复用模块、改造范围和运行命令。不允许先假设项目就是 Next.js，也不允许为适配本规范重写整个站点。

2.  保护现有成果：不删除已有页面、地图/路线、图片资源、样式主题、按钮功能和已可用 API；优先使用适配器和新模块接入。修改前记录当前 build、测试和关键页面截图作为基线。如现有框架不是 React/TypeScript，先沿用现有技术实现同等行为，确需迁移时记录原因与影响。

3.  功能按阶段实现：先完成结构化状态、组件稳定 ID、局部编辑和保真验证，再实现三候选生成、预览和提交；最后接入 AG-UI / A2UI，做自动化实验。每阶段完成后运行测试并提供可验证的结果。

4.  Agent 不能直接改产品源码以满足运行时用户指令：用户在三日游网页里说“修改第二天下午”时，运行时 Agent 只能返回声明式候选/补丁，不能生成任意 JSX/JS 执行、不能写入项目文件、不能跳过验证层。

5.  除被授权的范围以外默认禁止修改：所有候选必须从同一个 baseVersion 克隆；候选进入正式状态前都要执行路径权限检查、Schema 校验、状态差异检查、既有约束校验与必要的功能检查。模型输出永远不是提交权限。

6.  可运行优先：无法立即接通 LLM 时，应提供可切换的 mock Agent，演示同样的三候选、选区、锁定、预览和多轮版本机制，不阻塞前端与验证逻辑。

7.  有不确定性时不编造接口：依照仓库实际依赖选择包版本；接入 A2UI / AG-UI 之前先核对当前官方 API。标准不自动提供多目标优化和目标保真，这些属于本项目的自研逻辑。

0.1 最终交付物

现有三日游网页可继续访问，原功能通过回归测试。

编辑模式：组件悬停高亮、选区、组件锁定、修改范围提示。

每次合法编辑请求默认给出 3 个相互独立且具有差异的候选（不足 3 个合格候选时说明原因，不以无效方案凑数）。

三候选卡片支持差异摘要、独立预览、评分/排序、选择一个正式提交、放弃候选。

正式提交采用受限 JSON Patch，后端二次校验；未授权修改被拒绝并返回具体原因。

版本历史、撤销、恢复、连续多轮修改以及冲突处理。

测试与实验：对照基线、自动化回归、局部保真/多轮保真指标。

README、实现说明、运行方式、环境变量示例、测试记录与演示脚本。

1. 研究目标与可演示场景

1.1 研究问题

编号

目标

工程实现

成功判据

R1

多候选 GenUI

生成意图不同的 3 套受约束候选 Patch，再渲染可交互预览

候选均通过合法性检查；可比较且存在实质差异

R2

可控局部编辑

componentId、选择器、锁定、白名单、范围可视化

只允许改选区映射的数据/UI 字段

R3

目标保真

版本快照、不变量、差异校验、自动回归、并发冲突

未授权持久状态变化率为 0（在测试覆盖与规则范围内）

1.2 三个必须完成的端到端演示

场景 A：三候选。用户选择“第二天下午”，输入“行程太累，换成轻松安排”。系统返回：A 最小改动、B 交通效率优先、C 休闲体验优先；每个候选可独立预览，且未经确认不修改正式行程。

场景 B：选区和锁定。用户锁定第一天和第三天，只允许调整第二天下午的景点及时间。Agent 即使返回第一天的 Patch，也被服务器拒绝；前端显示“超出允许修改范围”，正式状态不变。

场景 C：多轮保真。先调整第二天下午，再调整预算呈现，再修改卡片风格，再返回第二天局部编辑。每轮查看 Diff；之前锁定的内容及未授权业务字段保持不变，支持撤销到任一已提交版本。

1.3 边界与优先级

P0 必做：现有网页保留、结构化数据、稳定 ID、选区锁定、受限 Patch、3 候选预览、选择提交、版本历史、自动化保真测试。

P1 建议：AG-UI 流式状态事件、A2UI 组件目录与适配渲染器、多目标评分、多样性去重、可视化 Diff、功能回归。

P2 研究增强：Pareto 排序、人类反馈统计、约束冲突解释、并行候选生成、较大规模的实验矩阵。

不属于本轮范围：实时预订机票/酒店、用户账号体系、多端同步、真实地图路径优化引擎的重新实现。已有相关能力应保持正常。

2. 体系结构与关键原则

┌──────────────────── 现有三日游网页（增量改造） ────────────────────┐│ 行程卡片/地图/预算 + 编辑模式 + 组件选区 + 锁定 + 三候选独立预览  ││                  ↓ 提交 EditRequest / 选择 candidateId          │└───────────────────────────────────────────────────────────────┘                        ↓ HTTP；可选 AG-UI / SSE┌──────────────────────── Agent 编排层 ───────────────────────────┐│ IntentParser → ScopeResolver → ConstraintResolver              ││              → CandidateGenerator (A/B/C 不同策略)              ││              → CandidateValidator → Ranker/DiversityFilter     │└───────────────────────────────────────────────────────────────┘                        ↓ 声明式候选 Patch + 说明┌────────────────────── 保真与提交层 ─────────────────────────────┐│ PatchGuard → SchemaValidator → InvariantGuard → StateDiffGuard  ││           → PreviewSandbox → AtomicCommit → VersionStore        │└───────────────────────────────────────────────────────────────┘                        ↓┌────────────────── 业务数据 / UI 描述 / 历史状态 ─────────────────┐│ TripDocument、ComponentRegistry、Constraints、Versions、Events   │└───────────────────────────────────────────────────────────────┘

单一真相源：已确认的 TripDocument + UI 描述 + 约束 + 版本存储是系统事实；对话文本是辅助上下文，不直接覆盖已确认状态。候选隔离：候选只在从同一快照克隆出的草稿中运行，永不原地修改正式状态。默认拒绝：未被明确授权的编辑路径一律禁止。安全分层：模型提建议，校验器判断合法性，用户批准，服务端原子提交。

2.1 A2UI 与 AG-UI 的角色

A2UI：声明式、可验证的 Agent UI 消息与组件 Catalog；适合把候选 UI 描述映射到受信任的现有 React 组件。建议验证官方 v0.9.1 规范及 React renderer；不要直接混用不同版本消息格式。

AG-UI：Agent-前端事件/状态通信协议，支持 STATE_SNAPSHOT、STATE_DELTA 等事件；可用于流式展示生成进度和状态同步。

项目自研：业务 Schema、编辑范围白名单、候选生成与排序、补丁审计、历史约束、版本管理、验收实验。

协议分界：AG-UI STATE_DELTA 是通信载荷，不等于业务编辑已经获批；必须走相同的应用级 PatchGuard/commit 流程。A2UI updateComponents 也不能绕过受保护界面区域的变更审查。

参考：

https://a2ui.org/specification/v0.9.1-a2ui/

https://a2ui.org/renderers/

https://github.com/a2ui-project/a2ui/blob/main/docs/public/guides/renderer-development.md

https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/state.mdx

https://www.rfc-editor.org/rfc/rfc6902

3. 数据模型：让组件、业务数据与交互状态解耦

3.1 规则

1.  行程内容与展示结构分离；不要让可修改的旅游数据散落在 JSX 常量里。

2.  每个业务实体、页面组件有持久稳定 ID；不要用数组下标或随机重生成的 ID 作为编辑目标。

3.  正式状态与编辑会话状态分离；selectedIds 属于前端临时状态，lockedComponentIds 和硬约束属于服务端权威状态并按版本记录。

4.  修改引用实体时用 ID 映射，不使用不稳定的数组位置来指向景点。

5.  派生信息（例如由行程总价计算出的汇总）应标注为 derived，不能将显示变化误判为对其源字段的未授权写入；但视觉影响仍纳入视觉回归。

3.2 推荐 TypeScript 类型（与现有项目字段做适配）

export type EntityId = string;export type ComponentId = string;export interface Activity {  id: EntityId;  title: string;  startTime: string;       // HH:mm，时间格式由 Zod 校验  endTime?: string;  description?: string;  placeId?: string;  transport?: string;  estimatedCost?: number;}export interface Day {  id: EntityId;  label: string;  activityIds: EntityId[];}export interface TripDocument {  schemaVersion: '1.0';  tripId: string;  version: number;  title: string;  budgetLimit?: number;  dayOrder: EntityId[];  entities: {    days: Record<EntityId, Day>;    activities: Record<EntityId, Activity>;  };  ui: {    componentCatalogVersion: string;    components: Record<ComponentId, {      id: ComponentId;      kind: string;         // registry 内允许的组件类型      entityId?: EntityId;      props: Record<string, unknown>;    }>;  };}export interface EditSession {  selectedComponentIds: ComponentId[];  hoveredComponentId?: ComponentId;  mode: 'view' | 'edit';}export interface PersistedConstraints {  lockedComponentIds: ComponentId[];  hardConstraints: Constraint[];}export interface Constraint {  id: string;  kind: 'immutable_subtree' | 'max_budget' | 'time_order' | 'custom';  targetId?: string;  value?: unknown;  active: boolean;}

不要把整个 TripDocument 当作一个可自由修改的 JSON 对象交给模型；模型只能写当前授权路径。持久化版本号由服务端维护，Agent Patch 不得修改 /version、/schemaVersion、/tripId、系统约束等内部字段。

3.3 组件注册表 ComponentRegistry

每个显示行程的 React 组件挂载 data-component-id，通过注册表解析它绑定的数据路径与允许的动作：

export interface ComponentBinding {  componentId: string;  kind: 'DayCard' | 'ActivityCard' | 'BudgetCard' | 'MapPanel' | string;  entityId?: string;  editablePaths: string[];    // 服务端配置的 JSON Pointer 前缀  protectedPaths?: string[];  parentId?: string;}// 示例：仅允许更新某个活动字段，不允许改整天或全局设置。const binding: ComponentBinding = {  componentId: 'card-activity-d2-pm',  kind: 'ActivityCard',  entityId: 'activity-d2-pm',  editablePaths: [    '/entities/activities/activity-d2-pm/title',    '/entities/activities/activity-d2-pm/startTime',    '/entities/activities/activity-d2-pm/description'  ],  parentId: 'card-day-2'};

选择组件 → 服务端解析注册表 → 与用户权限、锁定状态、编辑意图求交集 → 得到最终 allowedPaths。模型不能自行声明更多权限。

4. 交互流程与前端 UI

4.1 编辑模式

页面顶部增加“浏览 / 编辑”切换；保持原导航、地图及卡片行为。

鼠标悬停显示边框与组件名；点击选中；支持面包屑选择父/子区域，Ctrl/Command 多选（至少实现单选和锁定）。

被选组件有高亮边框；被锁定组件显示锁图标；右侧面板显示 允许修改范围、禁止修改范围、当前版本。

用户输入自然语言时展示当前选区标签，不允许无意中改动选区以外内容。

无选区时默认进入“明确全局任务确认”流程，不应悄悄将全页作为可修改范围；用户确认全局修改前仅可建议策略，不提交。

4.2 生成 → 预览 → 提交

[用户选中第二天下午] + [输入修改要求]             ↓       POST /api/edits/candidates             ↓   返回 3 个候选 (id, patch, explanation, warnings, score)             ↓  用同一 baseState 分别应用 Patch → 三个隔离预览             ↓   用户点击方案 B → 查看差异 → 点击“采用此方案”             ↓        POST /api/edits/commit             ↓服务端重验证 + baseVersion 一致性 → 原子提交 → version + 1             ↓   更新主页面 / 历史记录 / 持久约束

候选必须是可运行的局部页面/组件预览。预览必须使用与正式页面相同的组件代码及数据绑定；不能只展示 Agent 的自然语言建议。如果已有地图、外链或修改动作会触发副作用，候选预览采用只读/沙箱模式，避免在点击预览时发生真实操作。

4.3 交互异常

请求期间用户修改了页面：旧候选 baseVersion 过期，提交返回 409；可刷新后重新生成。

部分候选验证失败：仅展示合格候选及失败原因摘要；不能未经验证仍标为“可选”。

全部候选失败：维持原状态，提示锁定/约束冲突和可采取的修改范围调整。

生成超时/LLM 不可用：展示错误与重试入口；开发环境可切换 mock Agent。

用户撤销：恢复某已提交版本对应的状态和约束，形成新版本或明确执行历史回退，避免破坏审计链。

5. Agent 输出契约、候选生成与重排序

5.1 编辑请求示例

{  "tripId": "trip-demo",  "baseVersion": 4,  "request": "把第二天下午安排得轻松一点，保留第一天和第三天",  "selectedComponentIds": ["card-activity-d2-pm"],  "candidateCount": 3,  "clientRequestId": "req-20261028-001"}

服务端从数据库读取 authoritative baseState、组件绑定、锁定约束，计算允许路径；不能相信客户端上送的锁定清单或 allowedPaths。若安全边界需要鉴权，还须检查 tripId 归属及读写权限。

5.2 结构化候选格式

{  "requestId": "req-20261028-001",  "baseVersion": 4,  "candidates": [    {      "candidateId": "cand-a",      "strategy": "minimal_change",      "label": "保留景点，缩短游览时长",      "rationale": "尽量保留已有安排，只增加休息时间",      "patch": [        {          "op": "replace",          "path": "/entities/activities/activity-d2-pm/description",          "value": "缩短游览时长，安排一次休息"        }      ],      "evaluation": {        "valid": true,        "semanticFit": 0.85,        "functionalFit": 1.0,        "stability": 1.0,        "usability": 0.77,        "changeCost": 0.1,        "score": 0.86      },      "changedPaths": ["/entities/activities/activity-d2-pm/description"],      "warnings": []    }  ]}

评分是示例字段，不是已经测量出的真实分数。实际实现中 evaluation 必须由服务端验证器和评分器写入；不要信任 LLM 自报的通过率或分数。

5.3 多策略候选生成

同一 baseState 下要求 3 种不同目标：

1.  minimal_change：最小改动，优先保留原景点、原时间结构。

2.  efficiency_first：优先减少交通/切换成本，允许在授权范围内调整时间或交通；如无可靠距离数据，不宣称真实缩短了行驶时间。

3.  experience_first：优先轻松和可读性，适当增加休息、降低信息密度。

实现建议：为每个策略分别组织约束提示和生成任务；结果独立验证，比较 Patch 的语义/字段/最终渲染结果差异。若两个候选是同一结果换个文案，应去重并重新生成；不为凑齐数量牺牲合法性。

5.4 决策顺序：硬约束先行，多目标评分随后

第一关（必须全部通过）：请求/响应 Schema、Patch 语法与路径权限、锁定组件、业务不变量、提交版本、必要的功能约束。

第二关（只对合格候选）：

[Q(c) = w_sF_{sem}(c)+w_fF_{func}(c)+w_tF_{stable}(c)+w_uU(c)-w_cC(c)]

其中语义符合度 F_sem、功能保真 F_func、未授权区域稳定性 F_stable、体验得分 U、变更成本 C 均规范到 [0,1]；权重配置存储并可调。最小可行版本先用规则、结构差异和自动化测试评价，LLM 评分仅用于无法规则化的语义维度，必须标注不确定性。

第三关（多样性）：对补丁归一化并计算路径集合/Jaccard 差异、最终值差异；太相似则重试或降级说明。P2 再加入 Pareto 排序，不必首版就实现完整多目标进化优化器。

6. 关键核心：受限 Patch 与目标保真

6.1 为什么拒绝“重新生成整页”

整页替换会把未授权区域置于模型可写范围，难以验证局部保真。默认操作只能是作用于授权字段的 JSON Patch。真正全局 UI 主题修改要通过独立权限确认与范围规则，不得借“调整第二天”为由改全局 CSS。

6.2 JSON Patch 操作策略

MVP 仅允许 replace；必要时扩展 add、remove，前提是明确配置可编辑容器和后置不变量。

MVP 拒绝 move、copy（源/目标跨域复杂），拒绝根路径 "" 和任何内部元数据字段。

Patch 前做 JSON Pointer 解码并规范化（RFC 6901）；检查路径段，禁止伪造、前缀碰撞及原型污染关键属性，如 __proto__、prototype、constructor。

“路径命中”必须检查段边界，而不是朴素 startsWith('/day2')，以免误放行 /day20。

禁止使用补丁覆盖整个父节点来间接改写受保护子节点。执行后计算真实叶子级 diff，确保差异全部落在授权集合内。

不让客户端将 allowedPaths、lockedIds 作为可信授权来源；这些以服务端状态为准。

6.3 推荐守卫实现伪代码

async function validateCandidate(input: {  current: TripDocument;  patch: JsonPatchOperation[];  allowedPaths: string[];  constraints: PersistedConstraints;}) {  const { current, patch, allowedPaths, constraints } = input;  assertPatchLengthAndSizeLimits(patch);  assertAllowedOperationTypes(patch, ['replace']);  assertValidJsonPointers(patch);  assertNoPrototypePollutionSegments(patch);  assertAllDirectWritePathsInWhitelist(patch, allowedPaths);  // 注意：应用在不可变克隆上，validate 模式，不直接改 current。  const draft = applyPatchToClone(current, patch);  validateTripDocumentSchema(draft);  validateBusinessInvariants(draft, constraints);  // 包括全局元数据、锁定对象及未授权区的实际差异。  const changedLeaves = deepDiffToJsonPointers(current, draft);  assertOnlyAllowedLeafDiffs(changedLeaves, allowedPaths);  assertLockedSubtreesUnchanged(current, draft, constraints);  assertInternalMetadataUnchanged(current, draft);  return { draft, changedLeaves };}

以上是接口级伪代码；Codex 应将工具函数做成有独立单测的具体实现，而不是将它们当成已存在的库 API。

6.4 多轮一致性与版本提交

Trip V1 ──commit(A)──> Trip V2 ──commit(B)──> Trip V3   │                      │                      │  snapshot                snapshot               snapshot  constraints             constraints            constraints  diff                    diff                   diff

数据库至少保存：versionId、tripId、parentVersion、baseVersion、createdAt、userRequest、selectedComponentIds、candidateId、patch、documentSnapshot、constraintsSnapshot、validationReport。

提交协议：

1.  根据 candidateId 读取服务端缓存/存储的候选及对应 baseVersion，不要信任客户端重新发送的 Patch。

2.  比较数据库当前版本；不一致返回 409 VERSION_CONFLICT，禁止旧补丁直接覆盖新状态。

3.  再次重放验证候选补丁与当前服务端约束。

4.  在数据库事务里写入新版本与当前版本指针，执行提交前一致性检查；原子完成。

5.  向页面返回新 TripDocument 与 version，刷新界面和历史列表。

6.5 三种保真维度

保真类型

必须实施的校验

可以达到的保证

数据/语义

白名单、叶子 Diff、锁定路径、业务不变量

可对持久业务数据做严格约束（取决于规则覆盖）

功能

交互回归、按钮/地图状态、可访问性与基础交互测试

在测试覆盖的功能内发现回归

视觉/状态

非目标组件截图、DOM 几何、候选独立状态、版本快照

能检测布局外溢和部分视觉漂移，不能无条件证明像素级恒等

特别注意：修改第二天卡片高度可能挤动旁边的卡片，这属于可检测的间接布局变化，不能仅凭 JSON 字段未变就宣称所有 UI 状态完全不变。实验报告应分别统计“未授权持久数据修改”与“未授权视觉布局变化”。

7. API 接口约定

如项目已有 REST/GraphQL 接口，优先复用；以下是建议的契约而非要求改变全站路由。

接口

方法

说明

重要响应

/api/trips/:tripId

GET

获取当前 TripDocument、权威约束、版本

document, constraints, version

/api/edits/candidates

POST

解析范围并生成/验证 3 候选

requestId, baseVersion, candidates, rejected

/api/edits/commit

POST

提交已验证候选

newVersion, document, changedPaths

/api/trips/:tripId/history

GET

获取版本与 Diff 概要

versions

/api/trips/:tripId/restore

POST

恢复选定历史版本

newVersion, document

/api/trips/:tripId/locks

PUT

更新锁定与约束（明确用户授权）

constraintsVersion

/api/edits/stream

可选 SSE

流式进度、候选生成事件

AG-UI 兼容事件或适配事件

生成响应建议：acceptedCandidates[]、rejectedCandidates[]、effectiveScope、generationMode: 'llm'|'mock'、diagnostics[]、requestId。候选允许独立失败，不能因为一个候选非法导致整个应用崩溃。

标准错误码：INVALID_REQUEST、NO_SELECTION、SCOPE_DENIED、LOCKED_COMPONENT、PATCH_OUT_OF_SCOPE、INVARIANT_VIOLATION、SCHEMA_INVALID、VERSION_CONFLICT、CANDIDATE_EXPIRED、MODEL_UNAVAILABLE。HTTP 状态和结构化错误码要保持一致。

AG-UI 事件边界：如果接入 STATE_SNAPSHOT/STATE_DELTA，用于交互流/候选草稿状态更新；不允许把任何流式事件直接写入“已确认正式行程”。正式状态只由 /commit 或等价的可信服务端事务更新。

8. 目录结构建议（不强制照搬）

src/  features/agentic-editor/    components/      EditToolbar.tsx          # 编辑模式与锁定      SelectableWrapper.tsx    # data-component-id + hover/click      ScopePanel.tsx           # 范围及锁定可视化      CandidatePanel.tsx       # 多候选、评分/差异      CandidatePreview.tsx     # 独立草稿预览      VersionHistory.tsx       # 版本与撤销    state/      editorStore.ts           # 选区、预览态，不等于正式状态    schema/      tripSchema.ts            # Zod schema/TS types      candidateSchema.ts    services/      tripAdapter.ts           # 映射原有三日游数据      componentRegistry.ts      scopeResolver.ts      patchGuard.ts      invariantGuard.ts      stateDiff.ts      candidateGenerator.ts    # mock 与真实模型接口      candidateRanker.ts      versionStore.ts      a2uiAdapter.ts           # P1，可选      aguiAdapter.ts           # P1，可选    api/      editHandlers.ts    tests/      patchGuard.test.ts      constraints.test.ts      candidate.test.ts      multiround.test.ts      edit-flow.spec.ts        # Playwrightdocs/  implementation-audit.md  architecture.md  demo-script.md  evaluation.md

如现有目录与该结构不同，应根据项目习惯重命名和拆分，切忌只为满足形式而复制一套平行页面。

9. 建议执行阶段及每阶段验收

Phase 0：仓库审计和基线保存

浏览当前路由、UI 树、组件复用情况、数据来源、交互入口与构建脚本。

记录 npm/yarn/pnpm 实际使用的包管理器，安装/启动/测试方式及浏览器访问路径。

对现有三日游主要页面做截图、测试并记录现有问题。

输出 docs/implementation-audit.md 和简洁改造计划。

验收：现有页面可启动；没有未经说明的删除、迁移或破坏性改动。

Phase 1：结构化状态 + 局部编辑最小闭环（优先级最高）

完成 TripDocument 适配和 Zod 校验；保持原组件外观。

为一天、一时段、一张景点卡片增加稳定组件 ID。

编辑模式支持单选、锁定，显示修改范围。

手工固定 Patch 实现：只改第二天下午某个文字字段。

patchGuard 验证范围与锁定，拒绝越界写入；单测覆盖父路径覆盖、前缀碰撞、特殊字符、原型污染、版本内部字段。

验收：允许路径变更成功，第一/第三天保持数据一致；至少 1 次恶意越界补丁被拒绝且状态不变。

Phase 2：多候选独立预览 + 正式提交

mock Agent 先为 3 策略返回 3 个具有不同实际 Patch 的候选；随后接入可配置 LLM。

显示三个方案的标题、理由、变更字段、验证结果、评分。

三套草稿从同一快照生成；切换候选不污染主页面。

选择候选后走后端 commit；新增版本，支持放弃与撤销。

验收：同一请求至少出现 3 个合法而不同的 mock 候选，且仅被选择的方案进入新版本。

Phase 3：多轮保真与回归测试

版本历史、撤销、恢复、baseVersion 并发检查。

历史锁定/预算/时间不变量持久化。

Playwright 覆盖第二天局部改动、越界拒绝、候选隔离、历史恢复和 5～10 轮混合编辑。

非目标组件截图 + 几何位置对照；记录“直接数据变化”和“间接布局变化”两类结果。

验收：已覆盖的受保护数据在多轮测试里无未授权变化；旧候选提交返回 409；原页面主要功能可用。

Phase 4：协议接入与多目标优化

使用官方文档核对 A2UI v0.9.1 React renderer 与组件 Catalog（自定义 TripCard、MapPanel 等）；如兼容性成本高，保留自研 Schema → React 渲染器，报告说明协议映射与限制。

AG-UI/SSE 传输生成进度或草稿状态，保证正式提交仍受事务保护。

增加硬约束过滤、质量评分、多样性筛选、超时/失败降级。

实现 Baseline vs 方法组实验，输出表格与简要分析。

验收：多候选与选区、版本功能无回归；对比实验可复现，输出 docs/evaluation.md。

10. 测试矩阵与实验评价

10.1 必须自动化的测试用例

ID

输入与操作

预期结果

T01

选择第二天下午，修改描述

仅授权字段变化

T02

锁定第一天后，候选尝试修改第一天

拒绝，状态不变

T03

授权 /.../day2，Patch 写 /.../day20

前缀碰撞被拒绝

T04

通过替换父节点间接改锁定子节点

真实 Diff 检出并拒绝

T05

特殊 JSON Pointer 转义、恶意属性名

正确解码/拒绝危险路径

T06

3 候选分别预览，切换 A→B→C

主状态不变，候选互不污染

T07

选择候选 B 正式提交

只提交 B，版本 +1

T08

V2 候选在当前 V3 上提交

返回 409，不覆盖 V3

T09

连续 10 轮合法局部编辑

受保护持久状态不变，已确认约束保留

T10

修改第二天卡片导致第一天视觉移位

视觉回归记录间接影响

T11

LLM 返回不合法 JSON / 越界 Patch

拒绝，明确提示并可重试

T12

刷新网页、恢复历史版本

数据、约束与视图可复原

T13

原地图/按钮/导航基础用例

无功能回归

T14

候选相同仅文案不同

多样性校验判重或标记

10.2 研究对比组

A：整页自由生成。作为实验基线，不应成为生产模式。

B：整页生成 + Prompt“不要修改其他部分”。

C：仅局部 Patch + 白名单。

D：局部 Patch + 锁定/不变量/版本 + 多候选评估（完整方法）。

四组使用相同任务集、相同初始状态与模型条件；固定 seed（若模型支持）、记录模型版本与采样参数，至少多次重复报告均值和离散程度。实验既覆盖单轮也覆盖 5 轮、10 轮；设计多类任务（内容、布局、预算、锁定冲突）。

10.3 定义指标（统计口径不能模糊）

未授权修改率：发生未授权持久值变化的受保护字段数 / 测试涉及的受保护字段总数（同时报告“有任一未授权改动的轮次占比”）。

多轮约束保持率：在给定轮次后仍满足的原有有效硬约束数 / 原有有效硬约束总数。已被用户合法解除的约束不计作违例。

功能通过率：通过的自动化功能测试数 / 应执行的功能测试数。

候选合格率：通过硬约束检查的候选数 / 实际生成候选总数。

候选多样性：Patch 字段差异/最终 UI 结构差异/用户标注的方案差异，至少两种来源，避免只数文字差别。

用户交互成本：完成一次修改的点击数、澄清轮数及耗时；若无真实受试者，标记为模拟统计而非用户研究结论。

视觉漂移：非目标区域元素的边框变化、坐标漂移或截图差异；设定稳定的浏览器、视口、字体和动态元素屏蔽规则。

建议可视化：随对话轮数增长的未授权修改率折线图、四组方法的成功率柱状图、一次编辑的候选/目标差异示意图。

11. 非功能需求与安全措施

性能：候选生成是异步过程；页面不得被阻塞。支持取消、重试和候选生成阶段提示。首版优先功能可用，后续记录 p50/p95 延迟，不先宣称固定时延目标。

可靠性：无 LLM 凭据时 mock 模式可工作；服务器重启后版本记录不丢失；客户端断线不会自动提交草稿。

安全：LLM 输出全部经过 Schema 校验；不允许 JSX/HTML/JS 原样执行；渲染文本默认转义；外部链接/图片 URL 采用 allowlist 或安全策略；Agent 不持有数据库直接写权限。

审计：记录 requestId、tripId、baseVersion、有效范围、候选验证报告、提交人及版本。注意不记录 API 密钥和敏感数据。

无障碍：编辑模式支持键盘访问；选中、锁定状态使用可感知文本/ARIA，候选切换有焦点管理。

依赖策略：优先复用现有 UI、状态管理、数据库；新增依赖需能解释用途，锁版本并记录。不存在兼容包时用适配层，不为“看起来用了协议”破坏稳定项目。

12. Codex 执行和汇报约定

每个阶段都必须返回

1.  改动文件列表（新增/修改/删除）、主要设计决策、与旧代码兼容方式。

2.  可运行命令：安装、开发服务器、构建、单测、E2E。

3.  实测结果：命令、是否通过、失败原因及未解决项目；不得把未执行测试写成“通过”。

4.  截图/录屏路径（若项目运行环境允许）。

5.  仍需的环境配置与风险；任何新增权限/重大迁移先说明理由。

阶段门禁

只有 Phase 1 测试通过后才进入 Phase 2。

只有候选隔离和 commit 完整后才进入 Phase 3。

A2UI/AG-UI 是增强，不得阻断 P0 功能；如集成失败，保留稳定实现并写出已验证的原因。

严禁悄悄删除功能、用静态图片替代可交互预览、通过 mock 截图伪装真实候选、把未做的实验写成实测结论。

交付总结格式

阶段：Phase X完成：...变更文件：...运行命令：...测试结果：...可演示 URL/路径：...未完成与原因：...下一阶段最小计划：...

13. 可直接粘贴给 Codex 的首轮执行提示词

我已经有一个可以运行的三日游网页。请先阅读本仓库和《Agentic UI / GenUI 三日游网页：多候选、局部可控编辑与多轮目标保真技术实施规范》，只实施 Phase 0 + Phase 1，不要重做网页。具体任务：①审计当前项目并写 docs/implementation-audit.md；②将现有三日游内容映射到稳定 ID 的结构化状态，保持页面外观与交互；③实现组件单选/高亮、锁定与修改范围面板；④实现仅修改第二天下午某一字段的受限 JSON Patch 与服务端/可信边界验证；⑤写单元测试覆盖授权修改成功、锁定拒绝、越权拒绝、父节点覆盖等；⑥实际运行 build 和测试，报告结果。不要跳过测试、不要改动无关页面。Phase 1 验收完成后停止并汇报，不要未经确认自动开始 Phase 2。

第二轮执行时，将上段 Phase 1 改为 Phase 2，要求接入 mock 三候选、独立预览、选择提交、版本存储；再依次执行后续阶段。

14. 官方参考资料

1.  A2UI 官方说明及规格（v0.9.1）：https://a2ui.org/specification/v0.9.1-a2ui/

2.  A2UI Renderer 支持情况：https://a2ui.org/renderers/

3.  A2UI Web Renderer 开发指南：https://github.com/a2ui-project/a2ui/blob/main/docs/public/guides/renderer-development.md

4.  AG-UI 状态同步与 STATE_DELTA：https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/state.mdx

5.  AG-UI 事件模型：https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/events.mdx

6.  RFC 6902（JSON Patch）：https://www.rfc-editor.org/rfc/rfc6902

7.  RFC 6901（JSON Pointer）：https://www.rfc-editor.org/rfc/rfc6901

8.  Playwright 视觉回归：https://playwright.dev/docs/test-snapshots

9.  Playwright 组件截图断言：https://playwright.dev/docs/api/class-locatorassertions#locator-assertions-to-have-screenshot

最后强调：本课程设计的关键不是“Agent 可以写页面”，而是让 Agent 在明确授权范围内生成多个可比较的候选，且每一个候选在提交之前可验证、可回滚、可解释。正式行程只能经受约束的增量提交发生变化。