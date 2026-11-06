# 外部与人工证据合同

`readiness_audit.py` 不会因为某个路径存在就把人工或伦理工作视为完成。以下 JSON 只登记真实流程的结果，不能提前填写、复制示例或用 Mock 代替。

## 标注 pilot

将 `validate_annotations.py --packet ... --private-map ...` 的真实输出保存为 JSON，并通过 `--annotation-pilot` 传给审计器。至少要求：总样本、语义样本、候选批次均达到 20% 双标，20 个可比目标标签、Cohen's κ ≥ 0.70、目标 ordinal α ≥ 0.67；pilot 还必须覆盖至少 20 个双标候选相关性标签且候选 ordinal α ≥ 0.67，并有至少两名独立标注者。pilot 标签集可保持 `status: pilot`，不能写成正式结果。

## 伦理决定

`--ethics-record` 接受研究负责人根据真实书面决定创建的 JSON：

```json
{
  "status": "approved | exempt | not-required-documented",
  "authority": "作出决定的课程或机构",
  "determinationDate": "YYYY-MM-DD",
  "reference": "可核验编号或书面依据位置",
  "consentMaterialsReviewed": true,
  "dataRetentionPolicy": "批准的数据保存与删除规则"
}
```

仓库不提供“已批准”示例，避免把模板误当决定。敏感文件应保存在受控位置，公开 artifact 只保留允许披露的摘要。

## 用户研究 pilot

`--user-study-pilot` 接受 pilot 完成后冻结的摘要：

```json
{
  "status": "complete",
  "participantCount": 6,
  "primaryBehavioralEndpoint": "冻结后的名称",
  "primarySubjectiveEndpoint": "冻结后的名称",
  "usabilityScale": "UMUX-Lite",
  "sampleSizePlan": "基于 pilot 配对差值方差的计划",
  "stoppingRule": "与样本量计划一致的停止规则",
  "formalRecruitmentStarted": false
}
```

参与者级数据、联系方式和原始伦理文件不得放入公开仓库。审计通过只表示这些最小字段与范围存在，不评价伦理决定本身，也不替代统计或课程审核。

最终论文审计还要求通过 `--formal-dataset` 提供 960-episode 正式评分数据、通过 `--primary-analysis` 提供非合成且两模型诊断就绪的混合模型结果，并通过 `--user-study-results` 提供完成、去标识且带数据/分析哈希的 C–D 结果摘要。缺少任一项时 `paperReady` 必须保持 false；预注册或正式实验“可以开始”不等于论文证据已经存在。
