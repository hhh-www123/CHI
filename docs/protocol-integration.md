# Phase 4 协议核实与适配说明

核查日期：2026-10-31。仅实施到Phase 4，不开展正式对照/用户实验。本项目使用受限自建适配器，不宣称官方A2UI/AG-UI全兼容，也没有调用不存在的SDK API。

## 1. 官方来源与版本

- [A2UI v0.9.1官方规范](https://a2ui.org/specification/v0.9.1-a2ui/)：核查时标为Current，v1.0为Candidate。采用v0.9.1的version/createSurface/updateComponents envelope形式；不混用v0.8的surfaceUpdate/beginRendering。
- [官方Renderer矩阵](https://a2ui.org/reference/renderers/)：React对v0.9.1列为Stable。不能声称官方不支持React。
- [官方React Renderer源码package.json](https://raw.githubusercontent.com/a2ui-project/a2ui/main/typescript/renderers/react/package.json)：当次main内容name=@a2ui/react，version=0.14.0，peer React18/19、Zod^3.25.76。
- [AG-UI官方事件文档](https://docs.ag-ui.com/concepts/events)：生命周期、CUSTOM、STATE_SNAPSHOT与STATE_DELTA结构分别核对。
- [AG-UI官方core源码package.json](https://raw.githubusercontent.com/ag-ui-protocol/ag-ui/main/sdks/typescript/packages/core/package.json)：当次main version=1.0.3，agui.protocolVersion=1.0，peer Zod3.25.18或4。

上述package版本是核查的官方main源码标注，不冒充本项目已安装包或保证npm发布包与main完全相同。此前尝试旧路径renderers/react/package.json返回404，已根据官方矩阵链接找到typescript/renderers/react；没有据错误路径编造库方法。

本地实际安装版本：

```json
{
  "frontend": {
    "react": "19.3.1",
    "react-dom": "19.3.1",
    "vite": "7.3.7",
    "typescript": "5.9.3",
    "zod": "4.1.12",
    "lucide-react": "0.468.0"
  },
  "python": {
    "fastapi": "0.156.8",
    "pydantic": "2.14.1",
    "httpx": "0.28.1",
    "jsonschema": "4.26.0",
    "uvicorn": "0.54.0"
  }
}
```

当前React兼容官方React peer范围，但项目Zod4.1.12不满足该Renderer的Zod3 peer；直接接入需要额外版本/桥接与现有卡片Catalog工作。选择自建最小只读Catalog复用StopCardContent，不降级Zod、不迁移页面。AG-UI SDK的Zod peer可兼容，但本轮仅需生命周期和自定义事件，采用显式校验的SSE适配；两套官方SDK均未安装，锁文件及原依赖保持。

## 2. A2UI声明式子集

Catalog ID：`urn:travel-genui:readonly-catalog:1`。当前只注册TripCard和ActivityCard；BudgetCard/MapPanel继续使用正式页面原组件，未提供候选协议组件。Catalog标识是约定ID，不进行远程下载。

服务器将已通过PatchGuard的候选转换为两个顺序消息，例如：

```json
[
 {"version":"v0.9.1","createSurface":{"surfaceId":"p2-<server-id>","catalogId":"urn:travel-genui:readonly-catalog:1"}},
 {"version":"v0.9.1","updateComponents":{"surfaceId":"p2-<server-id>","components":[
  {"id":"root","component":"TripCard","children":["activity-0"]},
  {"id":"activity-0","component":"ActivityCard","activityId":"activity-<stable-id>"}
 ]}}
]
```

`protocol_adapter.py`只从验证后的文档、有效选区生成配置，不接受模型自写UI源码。`protocol.ts`用严格Zod检查版本、Catalog、surfaceId、根节点、数量、顺序、唯一children和实际候选实体ID，拒绝未知属性、组件、HTML、JS、onClick、CSS、href及跨选区绑定。这里的activity-0仅为候选Surface内局部展示节点，长期业务ID与权限仍使用稳定activity UUID，不依赖数组索引授权。

CandidatePanel消费声明式组件映射，再复用原卡片内容和现有只读数据绑定。文本由React转义，候选不渲染外部链接/地图/可编辑控件，因此不接受协议URL属性。原页面链接安全过滤、地图查询和原生按钮保留。

限制：仅支持一次createSurface后完整updateComponents的只读两消息投影，不实现通用增量Surface编辑、updateDataModel/deleteSurface、动态路径/表达式/函数、用户action、Basic Catalog、主题扩展、A2A/MCP协商或官方Renderer一致性测试。组件值从验证后的候选TripDocument与本地可信目录绑定；这是项目自定义Catalog，不能宣称实现了全部官方数据绑定。

## 3. AG-UI事件与SSE边界

沿用`POST /api/sessions/{id}/edits/candidates`，请求仍是本项目CandidatesBody，并非官方RunAgentInput。`Accept: application/json`或省略时保留原JSON响应；`Accept: text/event-stream`时返回SSE，每帧`data: <JSON>`后空行。

已映射：

| 事件 | 属性 | 本项目用途 |
| --- | --- | --- |
| RUN_STARTED | threadId、runId | 服务器生成运行ID，threadId为当前tripId |
| CUSTOM | name、value | 解析范围、调用模型、验证、候选到达、最终批次 |
| RUN_FINISHED | threadId、runId | 完整批次已返回、当前运行结束 |
| RUN_ERROR | message、code | 安全中文错误及语义状态码，不返回堆栈/上游响应 |

CUSTOM名称：scope.started / scope.validated / generation.started / generation.finished / generation.retry / validation.started / validation.finished / candidate.validated / candidates.complete。最后一个value是经过独立Zod、真实Diff和Catalog复核的批次；candidate.validated只报告服务器已验证候选的ID、策略和推荐次序。

这是实际执行阶段回调，不是计时器伪造百分比，不宣称模型token级流式输出。模型等待时每秒可发送SSE注释keepalive；本地Mock也走相同边界。所有候选仍从同一权威快照生成，网络调用不持Store锁，生成前后版本复核。

STATE_SNAPSHOT仅接受previewOnly=true及baseVersion/candidateIds的草稿元数据，并丢弃不用；本轮服务器不发送它。STATE_DELTA未实现并显式拒绝，不能修改正式TripDocument。未知事件、错误顺序、错误runId、重复批次、过大事件、非法JSON、缺少RUN_FINISHED均报错并丢弃前端草稿。没有接入正式状态订阅或任意状态Patch执行。

客户端支持UTF-8分块及LF/CRLF帧，限制每次最多100事件、事件2MB、总流5MB；完整结束后才加载可采用候选。任何流式事件和A2UI更新都没有提交权限，只有用户明确采用后，原commit从服务器缓存取Patch再次检查范围/锁/版本并经SQLite事务保存。

SSE发出响应头之后的业务失败使用HTTP200中的RUN_ERROR，code包含503/409等语义码；流开始前的归属、请求格式、来源限制仍是普通HTTP错误。客户端识别两种错误，测试不将HTTP200冒充生成成功。

取消关闭前端读取、废弃迟到结果；后端每个阶段检查停止标记，另有4槽并发限制。同步上游httpx调用无法被浏览器取消立即打断，可能等到上游返回/超时，期间占用槽位；已保存但用户未收到的内存候选最多500条且按TTL失效，不写正式库。未实现断点续流或自动重连重放；用户重新生成。

## 4. 硬约束优先、评分与多样性

原Schema/PatchGuard/锁/不变量/实际叶Diff/全量业务Diff先行，不合格候选没有分数、Surface或提交ID。模型输出仍只有strategy与Patch，自报evaluation/score/权限字段拒绝。

`ranking_weights.json`保存规则权重：语义匹配0.30、结构功能0.20、授权稳定性0.20、可用性0.20、少改动0.10。五项归一化[0,1]，加权求和，服务端生成rank与完整依据，客户端不参与授予权限。A/B/C保留策略顺序，另显示推荐次序，不为了得分颠倒用户选中的方案。

- semanticFit：需求关键词与实际改变字段的匹配代理，匹配0.8，否则0.4；不声称理解全部语义。
- functionalFit=1：仅指该候选通过结构、路径及不变量检查；不是对所有按钮语义的自动证明。原功能另外通过E2E回归验证。
- stability=1：通过守卫的未授权持久字段变化为0，不代表所有非目标组件像素不移动。
- usability：0.5基础，休息描述+0.25、最大时间变动在10–75分钟+0.15，上限1；为可复现启发式，非用户研究结论。
- changeCost：直接变化叶数/授权叶数，上限1；changeEconomy=1-changeCost。

每个候选保留规则版本、指标、权重、变更成本、分数和文字证据；采用后将服务器评分及依据写入同一个版本审计记录，重启可查。未引入LLM自评分/Pareto或对照实验。重复识别仍比较实际最终文档、时点差及真实地点，同时提供变更路径Jaccard和最终状态相等指标。LLM不足三个时只在原快照、原选区与原字段内补充一次；重试输出先精确去重再经全部硬检查。Mock不伪装重试为模型，不足三个明确提示。

## 5. 观测与测试

结构化日志只写event/requestId/candidateId、耗时、数量、合格率和attempts，不记录Key、提示全文、原始模型响应。generationMs包含生成、验证、排序、候选准备耗时；qualifiedRate=合格数/精确去重后的提案数；commitMs包含正式重验与持久写入。实际样本见[观测日志](verification/phase4-observation-sample.json)。它们是开发测试观察，未开展正式实验。

协议/渲染/评分与实际结果见[Phase 4验收](phase4-acceptance.md)。错误事件测试证明正式状态不受污染；不把自建JSON/SSE适配表述为官方SDK全兼容。
