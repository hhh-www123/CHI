# Phase 2 验收记录

日期：2026-10-30。工作目录：`travel-genui_1/`。版本：`0.6.2-phase2`。**本轮仅Phase 2，完成后停止，未进入Phase 3/4。**

## 1. 规范与前置门禁

开始时根目录没有 `Agentic_UI_三日游_技术实施规范.md`；已阅读Phase 1审计、真实实现及留存的同名DOCX全文摘录，并据此补齐根Markdown，入口注明来源，没有伪造新规范。沿用现有框架、页面、资源与数据流；没有重新建站、迁移路由或替换数据库。

所有命令在副本根目录运行，测试服务使用专用数据库、空模型/地图Key，未让预览或测试写入正式行程。

| Phase 1前置命令 | 实际结果 | 证据 |
| --- | --- | --- |
| `pnpm build` | exit0，TypeScript+Vite7.3.7；1656模块，8.78s | [gate-build](verification/phase2-gate-build.log) |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase1-tests.ps1` | 235项通过，20.504s，无跳过 | [gate-backend](verification/phase2-gate-backend.log) |
| `node tests/trip-schema.cjs` | 12项通过，exit0 | [gate-schema](verification/phase2-gate-schema.log) |
| `$env:UI_ARTIFACT_PREFIX='phase2-gate'; node tests/phase1-ui.cjs` | 12组通过，无浏览器脚本错误 | [gate-browser](verification/phase2-gate-browser.log)、[JSON](verification/phase2-gate-browser.json) |

门禁确认结构化状态、选区、日/时段/卡片锁、受限Patch、数据库隔离预览、地图降级及独立滚动可用；不需要绕过或修复核心守卫。Phase 1旧自由代码浏览器脚本与本规范冲突，历史基线失败记录仍保留，未以其替代当前12组功能回归。

## 2. 已完成能力

- 新自然语言编辑链路使用同一权威快照、selectedComponentIds、baseVersion和服务端effectiveScope。默认三策略A/B/C；生成与预览不调用正式commit。
- Mock读取当前真实行程的描述与时点，生成实质不同的Patch；支持轻松、休息与节奏等明确规则，超出能力提示，不伪装模型。
- LLM适配器真正使用服务端httpx调用兼容Chat Completions接口，严格JSON提案；无Key、超时、认证/HTTP错误和非法输出明确报错，不偷偷切换Mock。
- 每个候选经过JSON Schema/Pydantic、PatchGuard、锁、不变量、实际TripDocument叶Diff、全量原业务Diff；客户端再以Zod及差异绑定复核。
- 服务器写candidateId、baseVersion、Patch、审核报告和TTL；拒绝模型自评合法/分数/权限/ID/代码。去重相同或高度相似时点方案，不足3个说明原因。
- 当前右栏显示策略、数量、字段变化、前后值和验证结果。真实只读预览复用原景点卡片内容及数据绑定，不执行外链、地图、编辑或保存。
- 主左栏保持正式Session；A→B→C和取消仅改变候选临时状态，正式数据、地图状态和锁保持不变。
- 提交只接受服务器保存的candidateId与baseVersion，重验当前约束，经原SQLite CAS原子写入，版本+1。旧版本409、过期/失效410、越权/锁定/非法补丁拒绝；失败无部分更新。
- Phase 1手工Patch、地图/备选/备注/时间、可信卡片与时间轴、历史/撤销/导出和原主题布局保留。版本、README和启动说明已同步。

## 3. 最终真实验证

| 实际命令 | 最终结果 | 日志 |
| --- | --- | --- |
| `pnpm build` | exit0，1659模块，2.70s；TS与Vite成功 | [phase2-build.log](verification/phase2-build.log) |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase2-tests.ps1` | **271项全部通过，27.890s，无跳过**；原235+新增36 | [phase2-backend.log](verification/phase2-backend.log) |
| `node tests/trip-schema.cjs` | 原12项通过；前置与开发后均复核通过 | [gate-schema](verification/phase2-gate-schema.log) |
| `node tests/phase2-schema.cjs` | **25项全部通过，exit0** | [phase2-client-schema.log](verification/phase2-client-schema.log) |
| `node tests/phase2-ui.cjs` | **9组通过，exit0**，browserErrors=[]，externalRequests=[] | [phase2-browser.log](verification/phase2-browser.log)、[JSON](verification/phase2-browser.json) |
| `$env:UI_ARTIFACT_PREFIX='phase2-regression'; node tests/phase1-ui.cjs` | **原12组继承回归全部通过，exit0**，无浏览器脚本错误 | [regression.log](verification/phase2-regression-browser.log)、[JSON](verification/phase2-regression-browser.json) |
| `$env:TRAVEL_LAUNCH_NO_BROWSER='1'; & '.\启动旅序.cmd'` | 副本8011启动为0.6.2-phase2，前后端修订与构建匹配 | [launcher.log](verification/phase2-launcher.log)、[修订验证](verification/phase2-live-revision.json) |

构建产物：`index-BXr1jCMt.css` 58.43KB、`index-DZCKiWGu.js` 375.27KB。没有新增依赖或改动锁文件。

新增36项后端测试重点：三策略独立快照、只提交B、锁定第一/三天时模型越权整候选拒绝、父选区不扩权、字段子集不足3说明、去重、不变量、无选区、缺Key、超时、乱JSON/截断/重复键、模型自评/代码拒绝、缓存候选篡改后重验、当前锁重验、TTL、归属、版本变化、双并发只有一次成功、生成期间不持锁。

LLM链路测试为 **真实httpx → 本地ThreadingHTTPServer的 `/v1/chat/completions` → 严格结构化候选审核**；不是把Mock适配器当LLM。HTTP fixture同时校验请求Authorization、模型参数与Prompt中的完整Schema引用。补验2项通过1.100s，见 [schema-reference-check](verification/phase2-schema-reference-check.log)，最终271项已包含这些断言。**本轮没有向真实服务商或高德发请求，因此不声称真实模型认证或地图联调已成功。**

初次新增后端测试中，测试桩误拦截httpx父类连TestClient请求也拦截，已缩小到适配器构造器并重跑通过；初轮失败保留在 [candidate-tests-initial](verification/phase2-candidate-tests-initial.log)。前端测试临时CommonJS后缀及目录清理问题已修正，最终25项通过。没有关闭验证器或放宽授权以让测试通过。

原 `test_agent.py` 仅两处阶段文案断言同步为“旧自由代码生成入口已停用”；零网络调用、状态不变和拒绝逻辑保留。原Phase1浏览器测试只添加证据文件前缀及实际阶段/版本元数据，12组行为断言没有减少；历史截图与报告未覆盖。

## 4. 必测用例对应证据

| 用例 | 实测结论 |
| --- | --- |
| 选第二天下午，输入“安排轻松一些” | Mock返回3个合法候选，A改1字段、B改2个时点、C改3字段；实际Patch不同 |
| A→B→C切换 | API正式Session逐字段一致，SQLite行JSON逐字节一致；锁、routeData、正式地图DOM无变化；预览无写请求或外部请求 |
| 只采用B | 正式TripDocument真实Diff恰为B的授权changedPaths；版本仅+1，第一/三天和锁不变；A/C未提交 |
| 伪造模型改锁定日 | 后端HTTP注入模型提案测试拒绝整候选；无候选可提交时422；原正式行程不变 |
| 客户端夹带Patch/allowedPaths/lockedIds | 提交请求extra禁止，422；不可把未审补丁送入commit |
| 旧baseVersion/失效candidateId | 409/410，原状态不变；提交重读服务器候选及当前约束 |
| 实际TTL过期 | 隔离服务设置30秒，等待真实服务器时钟过期，410；无部分写入 |
| 缺选区、缺Key、取消 | 前端中文提示明确；缺Key503无Mock回退；取消无正式数据变化 |
| 原功能、布局、手机 | 继承12组全过；桌面独立滚动、390px无横向溢出、没有可执行iframe |

地图测试为无Key降级与安全链接/状态回归，不能据此宣称真实地图服务可用。截图观察不等于像素级视觉恒等保证。

## 5. 截图

已打开图片人工核对：桌面1600×1100、手机390×844，原主题与卡片保留。截图仅为隔离演示会话，不是用户正式行程。

- [三候选与B真实预览/正式行程对照](screenshots/phase2-desktop.png)
- [方案A预览](screenshots/phase2-preview-a.png)
- [方案B预览](screenshots/phase2-preview-b.png)
- [方案C预览](screenshots/phase2-preview-c.png)
- [手机候选列表](screenshots/phase2-mobile-candidates.png)
- [手机真实卡片预览](screenshots/phase2-mobile.png)
- [最终继承回归桌面](screenshots/phase2-regression-desktop.png)

本轮没有录屏。所有截图路径均位于本仓库 `docs/screenshots/`。

## 6. 如何演示、切换模式

双击 [启动旅序.cmd](../启动旅序.cmd)，访问 [http://127.0.0.1:8011/](http://127.0.0.1:8011/)，确认版本0.6.2-phase2；启动脚本已实际运行并核对修订。首页打开三日演示 → 编辑 → 锁第一/三天 → 第二天下午 → 右栏输入“安排轻松一些” → Mock → 生成 → 切换A/B/C → 采用B。

配置只放服务端 `.env`，不提交源码仓库：

```dotenv
CANDIDATE_AGENT_MODE=mock
CANDIDATE_TTL_SECONDS=600
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=填写账号实际支持的模型标识
LLM_API_KEY=填写自己的服务端密钥
LLM_TIMEOUT=70
LLM_MAX_TOKENS=6000
```

`CANDIDATE_AGENT_MODE=llm`改变默认候选模式；网页也可显式切换。LLM使用现有OpenAI兼容接口配置；Key可由DEEPSEEK_API_KEY替代。无Key时Mock可用，选择LLM明确报配置错误。原 `AGENT_MODE=llm|demo` 仍仅控制既有城市规划链路，两者独立。`.env.example`已更新，真实 `.env`未修改。

候选接口沿用会话路由：`POST /api/sessions/{id}/edits/candidates`、`POST /api/sessions/{id}/edits/commit`；公开配置状态为`GET /api/edits/status`。详见 [architecture.md](architecture.md)。

## 7. 浏览器测试复现

在**独立PowerShell终端**启动下列专用服务，禁止改为8011/8000。它不加载正式.env、不使用正式数据库，不消耗第三方额度：

```powershell
$env:TRAVEL_ENV_FILE=Join-Path $PWD '.runtime/phase2-absent.env'
$env:TRAVEL_GENUI_DATA_DIR=Join-Path $PWD '.runtime/phase1-browser'
$env:AGENT_MODE='demo'
$env:CANDIDATE_AGENT_MODE='mock'
$env:CANDIDATE_TTL_SECONDS='30'
$env:LLM_API_KEY=''
$env:DEEPSEEK_API_KEY=''
$env:AMAP_KEY=''
$env:AGENT_REQUESTS_PER_MINUTE='120'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m uvicorn backend.app:app --host 127.0.0.1 --port 8013
```

确保指定的absent.env确实不存在。另一个终端在仓库根目录运行：

```powershell
pnpm build
node tests/phase2-ui.cjs
$env:UI_ARTIFACT_PREFIX='phase2-regression'
node tests/phase1-ui.cjs
```

需要可用的Playwright及Edge/Chrome；脚本优先读取本机模块或Codex已带运行库，也支持 `PLAYWRIGHT_MODULE_DIR`、`UI_BROWSER_PATH`、`UI_PYTHON_PATH` 覆盖。本机实测使用无头Edge，未读取个人浏览器配置。Phase2脚本强制8013和30秒TTL，SQLite只读核对；Phase1脚本仅对隔离库写入旧不可信代码测试fixture以验证其不执行。测试后可Ctrl+C停止8013；本次自管测试服务交付前已停止。

## 8. 所有新增/修改文件

以下清单不包含自动生成的dist、缓存、数据库与证据日志；完整机器清单见 [phase2-file-manifest.json](verification/phase2-file-manifest.json)。**没有删除已有产品源码、资源或测试。**

新增13个源码/测试/文档：

```text
Agentic_UI_三日游_技术实施规范.md
backend/candidate_agent.py
backend/candidate_api.py
backend/test_candidate_api.py
scripts/run-phase2-tests.ps1
src/components/StopCardContent.tsx
src/features/agentic-editor/CandidatePanel.tsx
src/features/agentic-editor/candidateSchema.ts
src/features/agentic-editor/candidateTypes.ts
tests/phase2-schema.cjs
tests/phase2-ui.cjs
docs/architecture.md
docs/phase2-acceptance.md
```

修改19个：

```text
.env.example
README.md
backend/access.py
backend/agent.py
backend/app.py
backend/config.py
backend/revision.py
backend/test_agent.py
docs/implementation-audit.md
package.json
scripts/run-phase1-tests.ps1
src/App.tsx
src/api.ts
src/features/agentic-editor/LocalEditor.tsx
src/styles.css
tests/README.md
tests/phase1-ui.cjs
启动旅序.cmd
启动旅序.md
```

其中package/revision/启动文案同步版本；测试runner加强隔离环境与Key清理；未新增包、未修改pnpm-lock.yaml。原项目70个受审计文件SHA256均未变化，副本.env也与本轮开始一致；核验结果记入机器清单。

证据新增：`docs/verification/phase2-baseline.json`、`phase2-gate-*`、`phase2-candidate-tests*`、`phase2-schema-reference-check.log`、`phase2-backend*`、`phase2-client-schema.log`、`phase2-build.log`、`phase2-browser*`、`phase2-regression-browser*`、`phase2-launcher.log`、`phase2-live-revision.json`、`phase2-file-manifest.json`、`phase2-document-check.json`和`docs/screenshots/phase2-*.png`。初轮证据与最终证据分开保留。

## 9. 已知局限与阶段停止

1. 沿用单选，仅第二天下午的描述/开始时间；不支持候选增删活动、改地点/预算、生成任意组件或全局样式。单卡、只有描述、锁太多或时间空间不足可能少于3个，明确说明。
2. Mock为有限意图规则；LLM自然语言语义是否完整满足需求仍需用户比较确认。校验严格保证已定义的数据/权限边界，不代表能自动证明所有自然语言目标或旅游可行性。
3. 相似性策略保守地合并各活动时间差≤5分钟的候选，也会合并仅描述不同的方案；没有Phase4语义评分/Pareto。
4. 效率策略没有路线优化、真实距离或交通时长保证。时间修改提交时会清理受影响日的派生路线缓存，需按原功能重新查询；预览不会改地图状态。
5. 候选为单进程内存，默认10分钟，重启/成功提交后失效；继续单worker，未做分布式共享候选。客户端到期提示依本机时钟，服务器单调时钟/版本判断是最终依据。
6. 共享预览在较窄右栏布局，移除了有副作用的控件；不宣称像素级等同正式大卡片。数据未变不等于所有视觉几何绝对不变。
7. 本轮没有真实模型/地图联调、Docker实测或公网发布；配置状态不等于认证成功。原文件保留，不启动或改造原travel-genui。
8. 沿用Phase1保守共享dayUI锁规则、旧生成锁与新组件锁区别，以及原有撤销/重置语义；没有借本轮新增历史恢复、多轮实验或协议事件写权限。

Phase 2已完成并停止。进入Phase 3需要用户另行授权；A2UI/AG-UI/SSE和多目标评分实验属于后续阶段，未接入、未宣称完成。
