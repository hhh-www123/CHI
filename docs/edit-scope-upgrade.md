# 解除Phase 1演示限制：范围扩展记录

日期：2026-10-31。工作目录travel-genui_1，版本0.6.4-scoped-editor。当前代码中的扩展已复核并重新构建、测试和确认启动。未重建页面、替换技术栈或进入Phase 4。

## 现在可以修改什么

- 不再固定第二天下午：任意现有日期、上午/下午/晚上和景点卡片均可选中；按选区生成服务端白名单。
- 可编辑描述、开始时间、真实目录中的placeId。地点字段需显式勾选“真实景点”，也可点击同时授权该字段的换景点示例。手工编辑支持真实景点下拉。
- 开始时间可跨上午/下午/晚上，仍需保持当天时间严格递增。服务端同步时段分组和卡片父归属，卡片稳定ID保持不变；不能移入已锁定时段。
- 地点替换由服务器验证真实目录、避免同日重复，并自动同步名称；无显式自定义描述时使用目录描述。模型不能直接写title、period或组件parentId。
- 修改时间或地点后只清理受影响日的派生路线缓存，需要重新查询路线；预览不会触发正式写入或地图副作用。
- LLM提示与客户端Zod同步扩展；Mock支持轻松节奏、指定单卡时间及真实景点替换。明确指定一个时点时可能只返回1个方案；合法备选不足3个时说明原因，不凑重复方案。

## 保留的操作边界

组件锁、时间顺序、预算硬约束、服务端字段授权、预览后采用、原子提交、旧版本409和持久历史继续生效。空选区不会授权整页；空时段无活动可改。跨日期修改需选择相应日期，预算/全局呈现仍使用独立确认区。增删活动、酒店调整、任意布局组件生成不是本次新增能力；不会执行模型生成的JSX/HTML/JS。

解除的是早期演示范围，不是删除整个PatchGuard。用户需要解除已有锁时，使用明确解锁操作；普通指令不会偷偷清空用户约束。

## 实现

`backend/patch_guard.py`取消day2/pm过滤，按注册表父子关系解析选区，加入placeId及受限的服务器派生更新。`trip_document.py`注册地点叶字段并维护跨时段成员关系。`candidate_agent.py`使用所选活动实际时段和真实目录，`candidate_api.py`分别记录直接与派生差异并审查。`editor_api.py`与前端字段类型、候选Schema、只读预览及手工表单同步支持三种字段。

模型提交的是业务Patch；由服务器重建title/period/parentId并检查完整原业务Diff、元数据预期变化、目标时段锁。客户端也按可信目录重建草稿交叉验证，不能仅信任服务端声明的derivedChangedPaths。

## 实际验证

| 命令 | 最终结果 | 日志 |
| --- | --- | --- |
| pnpm build | TypeScript和Vite通过，7.98s | [构建](verification/edit-scope-final-build.log) |
| powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase3-tests.ps1 | 301项通过，36.389s，无跳过 | [后端](verification/edit-scope-final-backend.log) |
| node tests/phase2-schema.cjs | 38项通过 | [客户端](verification/edit-scope-final-client.log) |
| node tests/edit-scope-ui.cjs | 8组通过，无浏览器脚本错误或外网请求 | [浏览器](verification/edit-scope-final-browser.log)、[详细JSON](verification/edit-scope-browser.json) |

构建产物index-CPo_9rAe.js、index-DR79bUT0.css。浏览器自管8015和UUID隔离数据库，明确空模型/地图Key；不写用户正式数据库。新增实际浏览器组：

- Unlocked first-day morning supports three real mock candidates; preview is isolated and only B is saved：通过。
- Valid time changes cross periods with stable IDs; moving into a locked target period is rejected：通过。
- Generalized dates preserve exact selected scope and reject direct writes to derived metadata：通过。
- Third-day evening supports genuine candidate edits without changing other days：通过。
- Manual Patch replaces a real catalog place; its title is server-derived and stable IDs are preserved：通过。
- Natural-language place replacement produces three distinct real POIs; candidate B alone enters persistent history：通过。
- Unknown POIs, object locks and stale versions remain enforced; reload preserves generalized edits and constraints：通过。
- Original map fallback, navigation and persistent history remain usable on desktop and mobile：通过。

既有回归证据保留：[原选区/锁12组](verification/edit-scope-p1-browser.json)、[候选9组](verification/edit-scope-p2-browser.json)、[多轮7组](verification/edit-scope-p3-browser.json)。本次重跑全部后端和新增UI链路，未把Mock当作真实模型联调。

初轮针对性测试有1项状态码断言失败：新增placeId已经是有效字段类型，未授权地点字段的错误变为403，而旧断言期望422。仅修正预期，仍验证拒绝及状态不变；后续103项针对性检查与最终全套通过。证据：[初次记录](verification/edit-scope-targeted-initial.log)、[针对性最终](verification/edit-scope-targeted-final.log)。

## 演示与截图

双击项目内启动旅序.cmd，确认0.6.4-scoped-editor，访问http://127.0.0.1:8011/。编辑模式选择第一天上午 → 输入“安排轻松一些” → 比较候选并采用；或选中单个景点 → 勾选真实景点 → 输入“换个景点” → 查看真实POI候选。输入具体时间时先选单张卡片。已有用户锁需要先解锁。

- [第一天上午候选](screenshots/edit-scope/first-morning-candidates.png)
- [第三天晚上候选](screenshots/edit-scope/third-evening-candidates.png)
- [真实地点候选](screenshots/edit-scope/real-place-candidates.png)
- [刷新后的历史](screenshots/edit-scope/mobile-history.png)

真实模型或高德服务本次未调用；需使用服务端已有密钥与有效模型配置。支持的结构化字段如上，不宣称能执行任意自然语言功能。

完整文件清单及原项目/.env不变核验见 [文件清单](verification/edit-scope-file-manifest.json)，实际运行修订见 [版本检查](verification/edit-scope-live-revision.json)。README和启动旅序.md已同步，历史Phase报告按当时验收边界保留。
