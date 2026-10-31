# Phase 0 + Phase 1 交付记录

日期：2026-10-29。版本：v0.6.1-phase1。工作目录为原项目同级的 `travel-genui_1`；仅本副本改动，未进入 Phase 2。

## 审计、复制与增量边界

实际规范是同级 `技术规范/Agentic_UI_三日游_技术实施规范.docx`；未找到同名Markdown。已全文提取为 [规范文本](implementation-spec-reference.md)，保留原文件。完整仓库审计见 [implementation-audit.md](implementation-audit.md)。

保留 React/TypeScript/Vite、FastAPI/Pydantic/JSON Schema、SQLite、原图标/主题/地图/页面和资源，不新增路由框架。复制包含既有源码、配置、依赖和已存行程；SQLite通过只读backup取得一致性副本，不复制原服务PID/日志。复制后的pnpm映射不能直接运行，已按现有锁文件离线恢复，后新增唯一依赖 `zod@4.1.12`。

原项目70个文件逐一SHA-256复核未变；证据 [文件清单](verification/phase1-file-manifest.json)。原服务8000/v0.6.0继续运行。副本正式服务8011使用独立本地SQLite和独立前端会话键；测试数据库都在隔离目录或临时目录，未用正式数据库作测试写入。

## 已实现的最小闭环

1. `tripState`继续是唯一业务事实源；只读适配成TripDocument，不引入第二套独立业务状态。UUID5由会话ID、既有dayKey和stop.id生成，不依赖数组位置或版本号。时段归属首次由服务端快照确定并冻结。
2. `uiSpec`和文档组件描述与业务字段分离。前端mode/selected/hover只在React临时状态；组件锁与硬约束在服务端保存，随现有版本/快照持久化。
3. 现有日面板、时段、景点卡片增加稳定data-component-id。支持浏览/编辑、悬停、键盘、单选、高亮、父级选择、日/时段/卡片锁；表单和地图点击保留。
4. 服务端注册表与选中组件、字段意图及权威锁求交集。MVP仅第二天下午的description/startTime，客户端不能上传allowedPaths或约束来扩权。
5. 仅replace，精确比较解码后的路径段。拒绝根/父覆盖、前缀碰撞、空段、错误转义、URI/百分号绕过、危险属性、元数据、其他op、重复路径、超限、错误类型及非有限数值。
6. 在克隆上验证JSON Schema、时间/预算等硬约束、锁、TripDocument叶差异和完整旧业务状态差异；投影回写仅允许被核准的stop字段。描述不清路线；时间只清目标日派生路线缓存。
7. 右侧固定Patch草稿不改变左侧正式行程或SQLite。明确确认后，重取服务端保存的Patch，重算权限和锁、重放验证，沿用原Store事务/CAS提交；旧版本409，整批失败不保存。
8. 新组件锁同时覆盖旧direct/apply等业务提交入口。原“生成锁”语义保留：限制生成/Patch，但原手工编辑仍可用；新“组件锁”更严格。显式用户撤销/重置是控制操作。
9. 与新规范冲突的旧HTML/JavaScript运行时已暂停：App不挂载旧surface，不因旧replaceStops隐藏原生卡片；实际模型scope/candidates/plan调用在创建网络客户端前503。结构化城市规划trip_plan保留。原模型代码和旧数据未删除，只是不执行。

## 具体文件清单

新增代码/测试：
- `backend/editor_api.py`
- `backend/patch_guard.py`
- `backend/test_editor_api.py`
- `backend/test_patch_guard.py`
- `backend/trip_document.py`
- `scripts/run-phase1-tests.ps1`
- `src/features/agentic-editor/LocalEditor.tsx`
- `src/features/agentic-editor/tripSchema.ts`
- `src/features/agentic-editor/types.ts`
- `tests/phase1-ui.cjs`
- `tests/trip-schema.cjs`

修改：
- `README.md`
- `backend/agent.py`
- `backend/app.py`
- `backend/revision.py`
- `backend/test_agent.py`
- `compose.yaml`
- `package.json`
- `pnpm-lock.yaml`
- `src/App.tsx`
- `src/api.ts`
- `src/components/TripSetup.tsx`
- `src/styles.css`
- `src/types.ts`
- `vite.config.ts`
- `停止网页.ps1`
- `启动旅序.cmd`
- `启动旅序.md`

新增文档与证据位于docs/，包括实施审计、规范摘录、原文件指纹、交付记录、测试日志及6张基线/阶段截图。没有删除产品源码、资源或旧测试文件。

## 实际执行与结果

| 检查 | 实际结果 |
| --- | --- |
| 原版pnpm build基线 | 依赖复制映射修复后通过，原JS/CSS资源指纹一致 |
| 原版后端基线 | 177 tests / 14.364s / OK |
| 旧tests/ui-smoke.cjs基线 | 前2项通过，第3项失败：Generate is not disabled for locked day；不是本轮新增回归 |
| 新Patch纯单元测试 | 35 tests / 0.876s / OK |
| 新HTTP/持久化/隔离测试 | 23 tests / 5.156s / OK |
| 最终全后端 | 235 tests / 22.459s / OK，无跳过 |
| 客户端Zod测试 | 12 checks / passed:true |
| 最终pnpm build | TypeScript检查+Vite生产构建成功 |
| Phase1 Playwright浏览器 | 12组全部通过，browserErrors=[]，退出0 |
| 启动旅序.cmd | 实际构建并启动8011，版本v0.6.1-phase1 |
| Vite代理探针 | 5174→8011通过；非法创建422，没有创建正式行程 |

最终全后端不是“原177项完全不改断言”：6项原模型编辑入口预期与明确停用要求冲突，已把它们改成拒绝、零上游调用、状态不变检查，没有跳过测试。原模型输出解析等纯fixture测试保留，其通过不代表旧自由生成在此副本启用。

第一次依赖复制的构建失败、首次停用旧模型后6项预期失败、旧浏览器基线失败均保留日志；最终成功日志不覆盖这些事实。Vite短探针在关闭时曾取消仍进行中的依赖扫描，不作为完整开发模式浏览器验证结论；生产构建与独立浏览器验证均通过。

## 可复现命令

从本目录打开PowerShell。正式启动用 `./启动旅序.cmd`，打开8011；停止副本用 `./停止网页.cmd`。原项目8000不受影响。

```powershell
pnpm install --frozen-lockfile
pnpm build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase1-tests.ps1
node tests/trip-schema.cjs
```

其中测试脚本自动创建临时数据库、禁用模型/地图密钥，结束后恢复环境并移除临时数据。不要在正式服务终端直接运行会设置测试环境的命令。

浏览器测试分两个新终端运行。终端A仅启动隔离测试服务：

```powershell
$env:TRAVEL_ENV_FILE = Join-Path $PWD '.runtime/phase1-absent.env'
$env:TRAVEL_GENUI_DATA_DIR = Join-Path $PWD '.runtime/phase1-browser'
$env:AGENT_MODE = 'demo'
$env:LLM_API_KEY = ''
$env:DEEPSEEK_API_KEY = ''
$env:AMAP_KEY = ''
$env:AGENT_REQUESTS_PER_MINUTE = '120'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m uvicorn backend.app:app --host 127.0.0.1 --port 8013
```

终端B：

```powershell
node tests/phase1-ui.cjs
```

测试脚本需要Playwright和本机Edge；本次使用Codex已有运行库，未把机器专有路径写进产品运行流程。若你的环境没有Playwright，先在测试环境安装；这不影响普通网页启动。测试结束在终端A按Ctrl+C。测试脚本有端口/demo/无地图Key/数据目录守卫，拒绝误用正式8011或原8000。

## 演示及截图

访问 [副本网页](http://127.0.0.1:8011/)，首页点击“打开三日演示（不调用模型）”；开启编辑模式，锁第一/三天，选第二天下午，选择活动和字段，填演示文本，校验预览后确认应用。锁定选区后，修改将被拒绝。刷新后选择继续上次行程验证保存。

- [基线桌面](screenshots/baseline-desktop.png)
- [基线手机](screenshots/baseline-mobile.png)
- [基线规划首页](screenshots/baseline-setup.png)
- [Phase1桌面选区和锁](screenshots/phase1-desktop.png)
- [Phase1只读草稿及确认按钮](screenshots/phase1-preview.png)
- [Phase1手机范围面板](screenshots/phase1-mobile.png)

[浏览器报告](verification/phase1-browser.json)明确区分数据一致与布局检查：预览前后正式GET和SQLite记录完全一致；非法补丁整态一致；第一/三天与未授权字段保持；左右栏独立滚动、390px无横向溢出。未测量所有视口或任意输入的像素恒等性。

## 局限、风险与阶段门禁

- 本轮仅单选、第二天下午描述/开始时间；不支持新增/删除活动、修改ID、全局主题或其他业务字段。时间不得离开冻结时段，且每天严格递增。旧数据若本身时间无效，会拒绝Patch而不擅改其他字段。
- 组件锁对共享日UI保守保护：锁子卡也可能阻止整日展示切换；显式撤销按已有快照恢复数据与约束；重置沿用旧业务恢复规则，旧初始快照未包含新组件锁时可能保留这些锁，需显式解除。
- 固定Patch仅内存预览，重启即失效；正式应用和锁已入SQLite，重启保留。现有历史保留上限50步，不新增Phase3任意版本恢复/实验框架。
- 旧surface数据保留但不执行，因此其自定义HTML/JS交互在副本暂停；原生行程、地图/路线及已有声明式块保留。
- 本轮未联网调用真实LLM或高德；地图验证包含原接口单测、地图按钮、无Key降级、安全链接及静态图接口明确503，不代表实时路线服务可用性测试。城市规划保留原接口，但此次不声称真实模型联调完成。
- Phase2三策略候选、独立候选对比/评分与新Agent契约未实施；A2UI/AG-UI未接入，任何流式事件均未被当作写权限。
- 单worker、匿名Cookie归属、无账号和跨设备同步等原有限制不变。稳定ID依赖既有stop.id保持唯一；原已有实体ID被改变代表另一实体，不能沿用旧选区权限。

进入Phase2前需用户另行确认：三种候选策略与首轮可改字段、先mock还是接入模型、候选比较/确认界面。用户未确认前停止在本阶段。
