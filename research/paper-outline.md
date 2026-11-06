# 论文结构与证据清单

## 1. Introduction

- 长程 GenUI 失真的问题与实例；
- 现有整页生成/Prompt 保持的不足；
- 研究问题、方法概览和三项贡献；
- 不提前写入尚未得到的效果方向或百分比。

## 2. Related Work

- Generative/Agentic UI 与可塑界面；
- 长上下文、多轮代理与交互式评测；
- Mixed-initiative interaction、适当依赖与用户控制；
- 可逆交互、版本、provenance 与 end-user debugging；
- 结构化生成、约束解码和安全 Patch。

需要补充系统检索流程、纳入标准和对比矩阵；现有 `docs/evaluation.md` 的参考文献只作为种子集合。

## 3. Distortion Framework

- 状态越权、目标遗忘、旧目标泄漏、版本错误、UI 结构/交互漂移；
- 目标账本、距离和正确遗忘的形式化定义；
- 主要指标与不可合成总分的理由。

## 4. System

- TripDocument、稳定 ID 与组件注册表；
- A–D 更新协议；
- D 的 Scope、锁、不变量、版本、候选与提交；
- 威胁模型、失败行为和不执行模型代码的边界。

## 5. Study 1: Automated Benchmark

- 任务构造、长度、干扰轮、模型和 Prompt；
- 独立真值与人工语义标注；
- A–D 对照、消融、统计计划；
- 主结果表、目标年龄曲线、生存曲线和失败案例。

## 6. Study 2: User Study

- C–D 被试内设计、参与者、功效、程序和受控错误；
- 行为、NASA-TLX、可用性、适当依赖和访谈；
- 定量结果与主题分析。

## 7. Discussion

- 哪类失真可由结构守卫消除，哪类仍需要语义理解；
- 安全与表达能力、候选数量与选择成本的权衡；
- 对 GenUI 工具设计和评测的启示。

## 8. Limitations, Ethics and Reproducibility

- 领域、模型、语言、样本与地图事实边界；
- 受控错误、参与者隐私与服务商数据处理；
- 任务、Prompt、代码、原始去标识数据和分析脚本的发布范围。

## 提交前必须存在的证据

- [ ] 冻结的 24 个核心任务与哈希；
- [ ] 可复现的 A–D 正式 run manifest；
- [ ] 至少一个真实模型的完整原始输出和失败记录；
- [ ] 人工标签、一致性与仲裁记录；
- [ ] 带区间和效应量的主结果；
- [ ] 用户研究许可、匿名数据和分析；
- [ ] 成功与失败轨迹各一条；
- [ ] Artifact appendix 与一键复现说明。
