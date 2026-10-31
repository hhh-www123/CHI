# Phase 0 仓库审计与 Phase 1 最小改造说明

审计日期：2026-10-29。工作目录：`travel-genui_1/`；原项目 `travel-genui/` 保持运行，禁止本轮修改。本文先记录真实改造前基线；Phase 1 最终改动与测试结果由实施完成后补充。

## 1. 规范来源与阶段边界

用户指定的 `Agentic_UI_三日游_技术实施规范.md` 未出现在同级目录。实际找到 `技术规范/Agentic_UI_三日游_技术实施规范.docx`（v1.0，2026-10-28），已读取全文，段落及表格文字抽取为 [implementation-spec-reference.md](implementation-spec-reference.md)。未将该文档中的后续阶段要求当成本轮授权。

本轮仅 Phase 0（审计和基线）+ Phase 1（稳定 ID 结构化适配、局部选区/锁定、只改第二天下午指定字段的受限 Patch）。不新增三策略候选、LLM 编辑、评分、多轮实验或 A2UI/AG-UI 集成。原项目已有的候选/历史实现属于基线，不表示本轮完成了规范后续阶段。

副本复制源码、资源、配置、依赖及已保存行程。SQLite 通过只读连接的 backup 获得一致快照；未复制原服务 PID/日志。测试、预览使用副本 `.runtime/` 下的独立临时数据库，未使用副本正式 `backend/.runtime/travel.sqlite3`，也不修改原库。

## 2. 实际技术栈与运行入口

| 领域 | 实际实现 | 复用决定 |
| --- | --- | --- |
| 前端 | React 19 + TypeScript，Vite；`src/main.tsx` 挂载 `App.tsx` | 不迁移框架、不重建页面 |
| 包管理 | `packageManager: pnpm@11.25.0`，`pnpm-lock.yaml`、`pnpm-workspace.yaml` | 继续 pnpm；保留 esbuild 安装许可 |
| UI | 自有 CSS，lucide-react 图标，浅绿/米白主题，SVG 山水插画 | 复用现有卡片、地图、按钮和资源 |
| 前端路由 | 单页根入口，无 React Router/Next.js；日期通过组件状态切换 | 不引入路由迁移 |
| 状态 | React `useState/useRef/useEffect`；Session 是当前已保存快照，输入草稿/候选另存 | 新选区只存前端临时状态 |
| 网络 | `src/api.ts` 封装同源 Fetch、超时、AbortController 和错误信息 | 增补现有 sessions API，不换通信栈 |
| 后端 | Python 3.12、FastAPI、Uvicorn、Pydantic、jsonschema、httpx | 继续既有校验与 HTTP 服务 |
| 数据库 | SQLite：`sessions(id, version, document)`，整份 JSON 快照 | 不建另一套数据库或 ORM |
| 地图 | `backend/maps.py` 对接固定 AMap 主机；POI/路线/静态图服务，前端宿主组件渲染 | 不重写路线引擎、不虚构坐标/距离 |
| 测试 | Python unittest + FastAPI TestClient；Node CJS + Playwright/Edge | 保留原测试，增补局部权限和安全测试 |

实际构建为 `tsc --noEmit && vite build`；基线实际 Vite 为 7.3.7。部署文件为 Dockerfile、compose.yaml；本轮未执行 Docker 构建或公网部署。

原 Windows 启动入口是 `启动旅序.cmd`，自动构建并启动后端，由后端挂载 `dist/`。`停止网页.cmd` 调用 PowerShell 停止脚本。`scripts/build-info.mjs` 与 `backend/revision.py` 以源码指纹核对前后端修订，不能误删。副本交付前需要将本地服务端口与原 8000 隔离，并同步更新副本启动说明。

## 3. 页面、组件与交互基线

- `src/components/TripSetup.tsx`：城市、1–14 天、预算、日期、人数、偏好，继续上次行程。默认先进入规划表单。
- `src/App.tsx`：品牌头部、导航、旅行摘要、日期标签、单个活动日期面板、景点卡片/时间轴、酒店/预算、备注、需求/候选栏、历史与重置弹窗、导出计划。
- `JourneyComponents.tsx`：交通路段、路线地图、声明式 note/checklist/table 内容块。地图链接经过 URL 策略处理，静态图由服务端代理。
- `GeneratedSurface.tsx` + `generatedSurfaceDocument.ts`：旧版本生成 HTML/CSS/JavaScript 的 iframe 沙盒及宿主桥接。文件保留作为旧实现资源，但执行链与本规范冲突，见第 7 节。
- `public/favicon.svg`、现有 CSS、SVG 插画、锁文件、测试服务均有实际引用，不删无关资源。
- 桌面左右栏独立滚动；700px 及以下单列。基线实测 1440px 无溢出，390px 文档宽度等于视口。

基线已有：更换真实景点/酒店、修改时间/备注/预算、添加交通地图、切换卡片/时间轴、锁定生成修改、生成预览/候选选择/应用、跨日期样式复用、撤销、历史、导出、重置、刷新继续行程。

## 4. 现有数据来源与数据流

`backend/catalog.py` 保存经过真实地点核实的杭州备用目录（带 POI ID、地址、来源），预算金额明确为估算。新城市由 `trip_setup.py` 查询 AMap 行政区与地点，再由配置模型从目录 ID 安排行程；`AGENT_MODE=demo` 是明确的规则演示，不能宣称真实模型已连通。

```text
规划表单/继续行程
  → src/api.ts → FastAPI /api/sessions
  → tripState + uiSpec + controlState + routeData
  → SQLite 快照 → 返回 Session + catalog + derived
  → App 渲染原生卡片、地图、预算

原需求输入 → /generate → 冻结范围 + 内存预览 → /apply
  → 版本复核 + 原子写入 + history/_undo → 新 Session

备注/时间/酒店/预算 → /direct → 校验 → 同一 commit/Store.save
地图按钮 → /routes → 地点与路线解析 → 明确 ready/partial/unconfigured 状态
```

`tripState.days` 是以 `day1...day14` 键组织的业务数据；每个 stop 已有持久 stop ID、placeId、startTime。`uiSpec.regions` 保存呈现选项、声明式 blocks 及旧 surface。`controlState.lockedRegions/hotelLocked` 是旧的生成锁；旧版不把生成锁当成人工输入锁。`derived` 根据景点、住宿、人数、天数计算，不能被 Patch 直接写。

`App` 中当前日期、目标日期、输入草稿、请求 busy 状态、尚未应用的预览与 iframe 就绪/错误属于临时 UI 状态。localStorage 只记录会话 ID；持久内容在 SQLite 中，不在浏览器或 React 源文件中。

`backend/access.py` 使用 HttpOnly 访客 Cookie 及归属校验隔离 session，检查请求来源、体积、频率与并发。`Store.save` 用 version 条件更新防止覆盖新版本。`commit` 保存旧 payload 到 `_undo`（最多 50 条），版本递增并写 history。旧候选保存在进程内存，重启即失效；仍按单 worker 运行。

## 5. 已阅读的现有 API 与可复用校验

健康、目录、模型状态；创建/读取 session；`generate`、`repair`、`copy-style`、`apply`；`direct`、`locks`、`routes`、正式/预览地图；`undo`、`reset` 均在 `backend/app.py`，无需迁移全站路由。

现有 scope.py 对日期、数据/UI 字段、锁与冻结权限做校验；但它的合同基于旧候选更新，不是本规范所需的 RFC 6901/6902 叶字段白名单。不能仅将旧范围对象改个名字冒充 PatchGuard。

## 6. 基线真实命令与结果

所有命令均在副本根目录执行；以下测试环境不加载真实 `.env`，不消耗模型/地图额度。

### 构建

首次 `pnpm build` 触发 pnpm 运行前依赖校验，复制后模块目录映射不适配，报 `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`；关闭运行前校验后重试，Vite 解析 rollup 报 `ERR_MODULE_NOT_FOUND`。这是环境复制后的依赖链接问题，未因此修改应用逻辑。

修复：在副本执行 `CI=true` 环境下 `pnpm install --offline --frozen-lockfile`，从已有缓存重建依赖映射。随后重跑：

```powershell
pnpm build
```

结果：通过，TypeScript 检查成功，1586 modules transformed，Vite build 7.55s。

基线输出 `index-CiHdxxBD.css` 49.03kB、`index-DvaW1lXf.js` 315.40kB，与原版已构建资源一致。日志：[初次失败](verification/baseline-build.log)、[绕过检查后失败](verification/baseline-build-retry.log)、[修复后通过](verification/baseline-build-fixed.log)。

### 后端回归

```powershell
$env:TRAVEL_ENV_FILE = Join-Path (Get-Location) '.runtime\baseline-no-env'
$env:TRAVEL_GENUI_DATA_DIR = Join-Path (Get-Location) '.runtime\baseline-tests'
$env:AGENT_MODE = 'demo'
$env:LLM_API_KEY = ''
$env:DEEPSEEK_API_KEY = ''
$env:AMAP_KEY = ''
$env:AGENT_REQUESTS_PER_MINUTE = '120'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s backend -p 'test_*.py' -v
```

真实结果：`Ran 177 tests in 14.364s` / `OK`。包括已有 API、模型结构/范围、候选数量、持久化、地图适配、跨日复制与个性化规划测试。日志：[baseline-tests.log](verification/baseline-tests.log)。这些测试以 mock/隔离状态执行，不表示真实服务认证或真实路线均已验证。

### 页面截图与已有浏览器测试

使用独立 `127.0.0.1:8012` demo 服务，数据目录 `.runtime/baseline-preview`，通过新建无头 Edge context 建立兼容三日会话。未打开个人浏览器配置，不接触正式会话。桌面/手机截图成功，无 pageerror，手机无横向溢出：

- [原规划首页](screenshots/baseline-setup.png)
- [原三日工作台：第二天，1440×1000](screenshots/baseline-desktop.png)
- [原三日工作台：手机390×844](screenshots/baseline-mobile.png)
- [页面指标与真实模式标记](verification/baseline-browser.json)

另外实际执行：

```powershell
$env:UI_BASE_URL = 'http://127.0.0.1:8012'
$env:UI_TEST_ARTIFACT_DIR = Join-Path (Get-Location) 'docs\screenshots\baseline-smoke'
node tests/ui-smoke.cjs
```

结果：**未全部通过**。初始杭州三日加载、备注保存及卡片→时间轴局部预览/应用这两项通过；第三项在锁定第二天后等待“预览修改”按钮 disabled 时失败：`Generate is not disabled for locked day`。没有浏览器 JS 错误。属于本次改造前已经存在的 UI 断言/入口策略差异，不能把该套浏览器回归写成通过。详见 [baseline-ui-smoke.log](verification/baseline-ui-smoke.log) 与 [失败报告](screenshots/baseline-smoke/smoke-results.json)。

基线临时 8012 服务（已确认命令包含 backend.app:app / 8012）完成截图后停止；原 8000 健康检查仍为 `ok`、v0.6.0。

## 7. 新规范与旧行为的冲突，以及最小改造方案

1. **自由代码执行必须停止。** 旧 README 和 Agent 允许运行时生成 HTML/CSS/JavaScript；即使 iframe 有隔离，也不符合本轮“禁止任意代码执行”。副本需要禁用旧自由代码生成/修复/执行路径，保留代码文件与存储数据，旧 surface 回退为原生可信卡片并明确提示。原项目仍保留旧行为。新 Phase 1 演示不调用编辑模型。
2. **用适配器接入 TripDocument。** 从现有 tripState/目录生成规范化 entities + dayOrder，组件描述单独保存在 ui/registry。稳定 ID 必须绑定 session 与既有 day/stop ID，不能绑定当前数组位置；原来初始化时出现的 stop 序号不能继续作为新选区的数组索引授权。移动/重排后同实体仍可识别。
3. **组件归属固定。** 日、上午/下午/晚间、景点卡片都有固定 ID 与 parent 关系；时段归属要冻结在服务端元数据中，避免改时间后授权对象悄悄换组。补丁不能修改身份映射和时段归属。
4. **状态分离。** selected/hovered/mode 只在前端；lockedComponentIds 和硬约束在服务端按版本保存；业务字段、UI 描述和派生数值分开。继续使用原 SQLite 事务和版本机制，避免并行的“双真相源”。
5. **增量选择器。** 在现有组件加 data-component-id、hover、focus、单选边框和锁图标；保留普通浏览模式及原输入、按钮、外链行为。右栏显示服务端返回的有效修改范围与禁止范围，未选默认无权限。
6. **小范围 Patch。** MVP 仅第二天下午绑定活动的 description/startTime 叶字段；可选字段必须与服务端注册表映射求交集。仅允许 replace，禁止父覆盖、根节点、数组索引授权、危险属性、内部元数据与特殊指针绕过。应用在克隆上，检查 Schema、不变量、锁与真实叶 Diff，整批失败不得改正式状态。
7. **最小预览确认。** 固定手工 Patch 先验证并显示只读结果，用户确认后由服务端重验并原子保存；不引入三候选生成/排序（Phase 2）。新增端点沿用 `/api/sessions/{id}/...`，继续经过现有 Cookie 归属和版本校验。
8. **回归和报告。** 先独立单测，再 API 测试、pnpm build 与浏览器操作。分别记录正式业务值未变与视觉布局变化，不能以字段一致宣称像素完全一致。

## 8. 本轮明确不作的工作与已知边界

不替换 React/Vite、FastAPI、SQLite；不重建旅游规划系统或全站页面；不迁移路由；不接通 A2UI/AG-UI；不做真实预订/支付、账号、多端协作、地图优化；不实施 Phase 2 的三策略候选和评分，也不声称真实模型编辑能力已经接通。

地图基线仅验证已有地图入口与无 Key 降级，不代表付费服务实际可用。旧 iframe 运行回归需改为断言“禁止执行/可信组件回退”，不能为了让旧代码执行测试通过而打开安全绕过。

## 9. Phase 1 实测完成结果

已在副本增量完成：稳定TripDocument投影、Zod与服务端Schema、日/时段/景点选择高亮、持久组件锁、服务端字段白名单、受限replace Patch、隔离预览、确认后CAS提交和旧接口锁保护。模型自由代码执行已停用；原生页面和资源保留。

最终全后端235项通过（22.459s），包括35项核心守卫与23项新HTTP安全/持久化测试；客户端Zod12项通过；TypeScript+Vite构建成功；Playwright12组全部通过，无浏览器脚本错误。6项不适用的旧模型入口断言明确改为停用/零网络/状态不变验证，无跳过。旧浏览器基线第3项失败仍如实留存。

正式副本：8011/v0.6.1-phase1；原8000/v0.6.0继续运行。独立8012/8013测试服务已停止。原项目70个文件指纹均未变，没有初始化Git。

具体新增/修改清单、完整运行命令、日志、截图、已知局限与阶段门禁见 [Phase 1交付记录](phase1-delivery.md)。结构化清单见 [file-manifest](verification/phase1-file-manifest.json)。本轮没有实施Phase2、协议接入或多轮实验。

## 10. Phase 2增量审计（2026-10-30）

用户已授权本轮仅Phase 2。先运行Phase 1门禁：构建、235后端测试、12客户端Schema检查和原12组Playwright全部通过，不需要修复核心守卫。证据分别为 `verification/phase2-gate-build.log`、`phase2-gate-backend.log`、`phase2-gate-schema.log`、`phase2-gate-browser.log/json`。

继续复用tripState事实源、TripDocument/注册表、单选/锁定、原PatchGuard和SQLite CAS。新增两个可切换候选适配器、同快照审核与去重、服务端ID/TTL缓存、候选确认提交；接口仍位于会话路径内，沿用归属中间件和限流。前端从原卡片最小抽出共享内容组件，右栏自然语言输入启用受限三候选，主左栏不渲染候选。原自由代码执行入口保持停用。

根目录同名Markdown在本轮开始时仍缺失，现已从Phase 1真实DOCX全文摘录补齐，并在入口说明来源。新增/修改文件、最终真实测试、运行和风险见 [Phase 2验收](phase2-acceptance.md)；实际模块和数据流见 [architecture.md](architecture.md)。没有迁移框架、路由或数据库，没有进入Phase 3/4。

## 11. Phase 3增量审计（2026-10-30）

本轮授权仅Phase 3，先读取Phase 2验收与真实日志，再复核已有Phase 3增量：原SQLite增加不可变版本表并与当前状态同事务保存；保留原TripDocument/锁/候选守卫，新增明确整日展示、全局预算展示/约束确认和完整快照恢复语义。没有重建页面、迁移路由或替换数据库。新UI继续使用原主题、导航和地图组件。

Phase 2前置记录为271后端、12+25客户端检查、原12组与候选9组浏览器均通过。最终Phase 3重新运行构建、292项后端测试及12+25客户端检查，通过；最终E2E及多轮叶Diff/间接几何结果见[Phase 3验收](phase3-acceptance.md)和[多轮报告](multiround-test-report.md)。只读审查确认旧历史只接入当前真实checkpoint，不能虚构过去快照；数据/约束恢复需明确确认并创建新版本。未进入Phase 4。

## 12. 用户授权解除演示范围限制（2026-10-30）

用户要求移除仍存在的Phase1限制。实际查到PatchGuard硬编码day2/pm、字段仅description/startTime、Mock日期词黑名单与下午时间窗口、前端相同提示。当前增量扩展到所有选中日期/时段/卡片，增加会话真实placeId、跨时段服务端派生、前端可信重放和派生变更审计；保留原安全守卫、持久版本、地图和页面。验证结果与完整改动清单见[范围扩展记录](edit-scope-upgrade.md)。
