> **当前副本：Phase 3 / v0.6.3-phase3（2026-10-30）。** 全后端测试：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase3-tests.ps1`；多轮E2E：`node tests/phase3-ui.cjs`，自管8014及UUID隔离库，包含实际服务重启。最终命令、所有失败记录、覆盖及截图见 [Phase 3验收](../docs/phase3-acceptance.md) 与 [多轮报告](../docs/multiround-test-report.md)。下方Phase 1/2记录按其日期保留，不代表本轮新增能力；当前不执行模型HTML/JS，未进入Phase 4。

> **Phase 2历史记录：v0.6.2-phase2（2026-10-30）。** 本文下方为历史实现测试记录，旧自由HTML/JS生成已停用，不能以那些历史记录声称本副本会执行模型代码。当前可复现命令与结果见 [Phase 2验收](../docs/phase2-acceptance.md)。
>
> 后端全套：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase2-tests.ps1`；客户端：`node tests/trip-schema.cjs` 和 `node tests/phase2-schema.cjs`。Playwright用独立8013、demo/mock、空Key、专用测试库，禁止指向8011/8000正式服务。`tests/phase1-ui.cjs` 继续验证原12组能力；`tests/phase2-ui.cjs` 验证三候选只读预览与采用。真实LLM HTTP适配采用本地HTTP fixture测试，不代表已调用真实服务商。

# 本地网页冒烟验证

先按项目 README 启动前端（5173）和后端（8000），再运行：

```powershell
node tests/ui-smoke.cjs
```

生产构建由后端在 8000 端口提供时，先在 PowerShell 设置 `$env:UI_BASE_URL = 'http://127.0.0.1:8000'`，再运行上面的命令。

需要本机 Node.js、Playwright 包和可用的 Edge/Chrome。脚本优先使用已安装的 Edge/Chrome，不下载浏览器，也不会使用个人浏览器资料。每次运行创建独立浏览器 context 与演示会话。

如果 Playwright 不在项目依赖中，可将 `PLAYWRIGHT_MODULE_DIR` 指向已安装的 Playwright 目录。Codex 的 Windows 捆绑运行环境可自动发现。可选环境变量：

- `UI_BASE_URL`：默认 `http://127.0.0.1:5173`。
- `UI_BROWSER_PATH`：指定本机浏览器可执行文件。
- `PLAYWRIGHT_MODULE_DIR`：指定 Playwright 包绝对路径。

验证七项关键流程：初始三日行程；备注保存后局部切换时间轴且第一天/酒店不变；生成锁定；解锁、更新与撤销；备选替换与预算派生；刷新恢复；390px 手机宽度无整页横向溢出且可打开、关闭操作记录。界面操作触发真实本地 API，再核对会话数据，避免仅检查按钮是否存在。

截图和结果保存在 `tests/artifacts/`。这项测试检查有限演示流程，不评价攻略合理性，也不代表真实大模型效果；内容质量与用户体验仍按技术路线中的人工评价表及用户实验评估。


## 第二版 Agent 链路测试

旧的 `ui-smoke.cjs` 需要后端显式设置 `AGENT_MODE=demo`。默认 `llm` 模式不会因为没配置密钥自动回退。

`agent-ui-smoke.cjs` 使用隔离的本地 HTTP 模型接口，不耗真实 API 额度。分别开三个终端：

```powershell
# 终端一：模型模拟服务
.\.venv\Scripts\python.exe tests/fake_provider.py
```

```powershell
# 终端二：测试专用后台（不要把这些假配置用于正式服务）
$env:AGENT_MODE='llm'
$env:LLM_BASE_URL='http://127.0.0.1:18080'
$env:LLM_API_KEY='local-test-fixture'
$env:LLM_MODEL='local-candidate-fixture'
$env:AMAP_KEY=''
$env:COOKIE_SECURE='false'
$env:TRAVEL_GENUI_DATA_DIR=(Join-Path (Get-Location) '.runtime/agent-test')
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

```powershell
# 终端三：独立浏览器验证；先完成pnpm build
$env:UI_BASE_URL='http://127.0.0.1:8001'
node tests/agent-ui-smoke.cjs
```

验证：用户原句中的“经典间”与“每天”经过模型接口返回三天计划；预览不修改数据；应用后备注/酒店/景点保留；一次撤销全部区域；新增通用清单；其他访客不能读取已知会话ID；手机布局无整页溢出。报告 `agent-results.json` 明确标注 `fixture: true`。

真实 DeepSeek 与高德必须用自己的 Key 另做联测。模拟测试通过不能证明真实模型一定正确理解所有说法，不能证明真实地图服务在当前账号下可用。测试结束关闭隔离服务，使用新的终端启动正式后台，避免遗留测试环境变量。


## 真实地点与路线联测

`real-places-ui.cjs` 对接已配置的真实高德服务，覆盖7组页面流程：15个真实景点和3家酒店、常驻下拉及同日去重、换点保留状态、真实地图图片、自动刷新/撤销、全部日期路线、390px布局。此脚本不发起模型请求。

请仅使用隔离数据目录与8001端口运行，避免测试修改个人行程：

```powershell
$env:TRAVEL_GENUI_DATA_DIR=(Join-Path (Get-Location) '.runtime/real-places-verification')
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

在另一个终端运行 `node tests/real-places-ui.cjs`（需要先构建前端）。测试会调用高德API，需要有效的Web服务Key及可用额度；不得把它混同于使用Mock的后台单元测试。用完后关闭隔离服务。

2026-10-25验证：44项后台模拟边界测试通过；上述7组页面流程连接真实高德通过。另以隔离会话对真实DeepSeek完成一次景点地址表格生成并核对原行程未改。截图和测试数据为可清理临时产物，不作为应用运行依赖。


## 多候选界面与范围保护验证

当前 `fake_provider.py` 支持“范围理解”和“候选生成”两个阶段；其响应仅是确定性测试数据。沿用上面的模拟接口、隔离数据库及8001端口步骤，先构建前端后运行：

```powershell
$env:UI_BASE_URL='http://127.0.0.1:8001'
node tests/candidate-ui-smoke.cjs
```

该测试核对3个不同候选、左侧只读真实预览、候选与日期切换不写状态、取消恢复原界面、任选应用后未授权字段不变、兄弟方案过期、一次撤销、固定要求只返回1个结果、修改需求或生成失败后不能应用旧结果、390px移动布局及跨访客隔离。`UI_TEST_ARTIFACT_DIR` 可指定截图和结果的输出目录，默认 `tests/artifacts/`。报告明确标记 `fixture: true`、`realModel: false`、`realMap: false`。

后台 `test_candidates.py`、`test_scope.py` 与完整回归命令一起运行，使用临时SQLite和模拟响应。它们验证字段/日期/单个景点/既有内容块的范围约束、明确整天保护、重复结果过滤、路线复用、版本冲突和应用时再次校验；不能代表真实模型对所有自然语言的理解质量。

2026-10-27另以隔离数据与真实DeepSeek、高德配置验证了一次“仅给第二天增加交通与地图”的完整网页链路：模型返回3个不同布局，候选专用地图代理加载成功；任选应用后其他日期、景点、时间、备注、酒店和费用显示保持，撤销恢复原状态。真实联测不属于无额度回归，截图、临时测试行程与报告可在核对后清理。


## 开放式代码界面验证（v0.5.0）

更新日期：2026-10-28。`open-ui-smoke.cjs` 使用专门的 `open_provider.py`，与旧版 `fake_provider.py` 不同；两者不能混用。以下操作均在项目目录进行。

终端一启动模拟模型（18081端口）：

```powershell
.\.venv\Scripts\python.exe tests/open_provider.py
```

终端二使用独立数据库启动测试网页，先完成前端构建；以下配置覆盖本地真实Key，不调用真实模型或地图：

```powershell
pnpm build
$env:AGENT_MODE='llm'
$env:LLM_BASE_URL='http://127.0.0.1:18081'
$env:LLM_MODEL='fixture'
$env:LLM_API_KEY='fixture-only'
$env:AMAP_KEY=''
$env:COOKIE_SECURE='false'
$env:AGENT_REQUESTS_PER_MINUTE='120'
$env:TRAVEL_GENUI_DATA_DIR=Join-Path $env:TEMP ('travel-open-ui-'+[guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

终端三运行浏览器回归：

```powershell
$env:UI_BASE_URL='http://127.0.0.1:8001'
node tests/open-ui-smoke.cjs
```

覆盖重点是：真实iframe执行；超出旧组件列表的交互界面；预览不保存；应用后勾选等交互状态保存与刷新恢复；后续修改保留原行程及既有状态；运行错误反馈与有上限的修复；景点增删重排、日标题/日期、酒店/预算/晚数的授权与数据保存；范围外变化拒绝、锁定及撤销。

iframe只授予 `allow-scripts`，没有宿主同源权限；普通外部资源与联网库没有接入，地图使用宿主代理的真实图片。验证需分别检查脚本事件、宿主消息来源和数据边界，不能只检查源码是否出现某个按钮。生成代码加载时不得自动写业务数据；真实用户操作才能通过宿主数据接口保存。

模拟模型返回确定性代码，适合回归应用执行链路，报告应标注 `fixture: true`。真实模型对陌生需求的理解、代码质量、攻略事实与地图服务可用性仍需另做真实联测，不能用模拟通过替代。对复杂生成结果，应同时检查页面是否运行、要求是否满足以及未授权内容是否保留。

### 2026-10-28 真实模型联测

使用已配置的 DeepSeek（接口模型 `deepseek-flash`），在独立测试数据库中通过真实网页连续完成三轮：

1. 从真实景点目录生成两个不同布局的搜索/室内户外筛选面板；实际输入搜索词后结果变化。
2. 保留上一轮搜索与筛选，增加3项可勾选准备清单；应用后勾选，经后台保存并刷新浏览器，状态仍保留。
3. 保留搜索、清单及已保存勾选状态，再增加人数×人均费用的聚餐计算器；输入3人、80元，实际得到240元。

每轮均核对原行程、其他日期和生成预览不写入状态；最终桌面与390px手机布局无整页横向溢出，浏览器运行错误为0。本次新功能联测未请求真实地图，不能据此宣称地图服务也重新实测通过。结果只证明这些实际需求可完成，不代表任意需求均成功或完成了用户研究。

## 候选数量开关（v0.5.1）

“是否生成候选”默认未勾选。网页会在生成请求中明确传入 `generateCandidates: false` 或 `true`；前者成功响应恰好1个，后者至少3个不同且通过范围校验的方案。切换开关清除旧预览，运行错误修复沿用原请求的选择。

后端兼容旧客户端省略字段时的自动候选逻辑；接口拒绝用字符串、数字代替布尔值。数量不足会在原范围内有上限地补充生成，最终仍不足则报错，不能降低数量或复制结果。

`test_candidate_mode.py` 覆盖接口数量、补充生成、去重、越界、状态保留与修复模式；`candidate-mode-ui.cjs` 对接独立模拟服务验证复选框、实际请求、候选切换与应用、撤销及手机布局。它们属于模拟回归，不代表真实模型对任意要求均可完成。
2026-10-28 本轮验证：完整后台128项测试、候选开关浏览器回归、原候选比较及开放式交互回归全部通过。另使用已配置的真实DeepSeek，在独立数据库和真实网页中分别提交同一清单需求：未勾选返回1个、勾选返回3个；三个候选均实际执行并可勾选，任选应用后原行程与其他日期保持，浏览器运行错误为0。本轮真实模型验证未请求真实地图，不能将其计为地图联测。
## 左右独立滚动验证（v0.5.2）

2026-10-29使用隔离数据库和模拟模型完成浏览器实测：1440×1000、1440×768、1024×600、701×800和1440×500五种桌面尺寸下，左右滚轮、PageDown、触底均互不影响，整页保持固定，两栏底部可达。生成预览仅定位左栏，“返回方案选择”仅定位右栏；工作台锚点、输入聚焦、第三候选应用、撤销和历史弹层正常。700px与390px使用单列页面滚动，无横向溢出，浏览器运行错误为0。本轮使用模拟服务，没有调用真实模型或地图。
## 城市规划与跨日期页面复用（v0.6.0）

新首页先收集城市、1–14天、日期、全程总预算、人数与偏好。已有会话显示“继续上次行程”，不再在首次访问时自动创建杭州行程。后端回归包含 `test_trip_setup.py`、`test_scope_transfer.py`、`test_personalized_api.py`，验证城市目录、动态日期、费用、源日期只读、目标数据与交互状态保留、锁定、版本、撤销与失败保护。

`personalized-ui-smoke.cjs` 必须使用隔离的 **demo** 服务，不调用模型和地图。新开PowerShell，在项目根目录执行：

```powershell
$env:AGENT_MODE='demo'
$env:AMAP_KEY=''
$env:TRAVEL_ENV_FILE=Join-Path $env:TEMP 'travel-demo-unused.env'
$env:TRAVEL_GENUI_DATA_DIR=Join-Path $env:TEMP ('travel-setup-ui-'+[guid]::NewGuid().ToString('N'))
$env:AGENT_REQUESTS_PER_MINUTE='120'
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

先构建前端，再在另一个终端执行：

```powershell
$env:UI_BASE_URL='http://127.0.0.1:8001'
node tests/personalized-ui-smoke.cjs
```

脚本验证新首页、5天3人费用、1天及14天、明确选择第二/三天复用、预览不写、应用撤销、刷新继续、规划失败保留旧行程、双栏滚动和390px布局。已有浏览器回归通过 `browser-session.cjs` 显式建立兼容会话并继续，继续验证原有编辑功能；这不代替新首页流程测试。

2026-10-29：177项后台回归通过；新规划流程及原候选开关/开放式界面回归通过。另在独立数据库、真实网页中用真实高德与DeepSeek完成成都4天、2人、总预算6000元的规划，取得该城市25个真实景点和8个真实住宿地点供规划选择；再只修改第一天为可折叠深绿界面，通过按钮与用户原句分别套用第二、三天，两天实际显示各自景点，第四天及全部行程数据保持；刷新后继续行程，布局和数据恢复，浏览器运行错误为0。还单独验证了该目录中一组相近真实地点的步行路线；未声称全部4天路线都已联测。临时行程与截图不属于应用依赖。