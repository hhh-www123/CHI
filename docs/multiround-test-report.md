# Phase 3 多轮保真测试报告

日期：2026-10-30。以下由本次真实Playwright报告生成；不以候选文案、模型自评或截图结果代替字段/约束验证。

工作目录：`travel-genui_1`；版本 `0.6.3-phase3`；runId `4060181e-8f94-416d-a235-0a6e13b80cc0`；同一tripId `df45e8430d2b48008ffa0e02acf8297e`。

## 1. 方法与结论

隔离服务为 `http://127.0.0.1:8014`，独立SQLite数据目录 `.runtime/phase3-e2e-4060181e-8f94-416d-a235-0a6e13b80cc0`；demo/mock、空模型和地图Key，无真实外部模型/地图调用。原始证据：[JSON](verification/phase3-browser.json)、[最终日志](verification/phase3-final-browser.log)。

同一行程在锁定第一/三天、启用时间顺序及3000元预算输入硬上限后，以v4为基线。前10轮为合法主序列，第11轮是其他客户端的明确整日样式修改，用来触发原页面的旧候选冲突；拒绝操作单独计数，没有冒充合法轮次。实际成功提交11次，直接未授权投影路径0条，原业务全量未授权路径0条；每轮锁和历史有效硬约束均保留。

逐轮同时检查：版本严格+1；真实TripDocument/展示/约束Diff落入测试预期授权；从服务端稳定ActivityCard映射出的完整原tripState叶Diff也不越权；第一/三天业务快照逐字段相等；服务器记录的状态与约束快照等于实际正式状态。预览/失败请求另核对API对象与SQLite行JSON逐字节一致。

## 2. 逐轮摘要

| 轮次 | 输入/操作 | 版本 | 投影实际变化数 | 原业务变化数 | 约束保持 |
| --- | --- | --- | --- | --- | --- |
| 1 | 第二天下午最小改动 | v4→v5 | 1 | 1 | 是 |
| 2 | 明确全局授权：隐藏预算明细 | v5→v6 | 1 | 0 | 是 |
| 3 | 第二天卡片精简样式 | v6→v7 | 1 | 0 | 是 |
| 4 | 调整第二天下午开始时间 | v7→v8 | 1 | 1 | 是 |
| 5 | 恢复第二天完整卡片样式 | v8→v9 | 1 | 0 | 是 |
| 6 | 返回第二天下午采用体验方案 | v9→v10 | 3 | 3 | 是 |
| 7 | 明确全局授权：显示预算明细 | v10→v11 | 1 | 0 | 是 |
| 8 | 第二天局部时间轴 | v11→v12 | 1 | 0 | 是 |
| 9 | 明确恢复历史快照并新建版本 | v12→v13 | 4 | 3 | 是 |
| 10 | 撤销刚才恢复并保留审计链 | v13→v14 | 4 | 3 | 是 |
| 11 | 另一客户端明确修改第二天样式以形成冲突 | v14→v15 | 1 | 0 | 是 |

第2/7轮预算展示是独立全局授权：用户勾选确认后只写`/presentationSettings/showBudgetDetails`。第3/5/8/11轮必须明确选择整天DayCard，不能以时段或单卡选区改整日共享样式。第9/10轮恢复/撤销是明示全快照控制操作，按目标快照同步恢复锁和硬约束；本次目标恰好保留了基线锁和预算上限。

“更改时段”本次准确实现为修改下午活动的开始时点（14:00），仍在原pm绑定内；没有实现跨上午/下午重新绑定活动，冻结时段ID的Phase 1边界仍生效。

## 3. 每轮完整授权、实际差异与审计

命名空间说明：候选与手工Patch用TripDocument；展示和恢复使用payloadSnapshot。测试的`allowedPaths`是独立预期变化集合；`serverAllowedPaths`才是服务端真实授权记录。恢复/撤销明确授权完整快照，因此服务器是快照根范围，测试仍额外用预期叶字段核对本次具体差异；不能把它冒充普通局部编辑。原业务映射使用稳定实体ID，不以当前数组位置授权。

### 第1轮：第二天下午最小改动

输入：安排轻松一些。v4→v5；约束保持：True。

测试预期允许路径：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/description`
- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`

实际投影叶级差异：

- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`

完整原业务差异：

- `/days/day2/stops/byId/day2-stop-2/description`

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "period-day-d84f0aeee1a85438a6b2984eabb9442c-pm"
  ],
  "serverAllowedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/description",
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "pathNamespaces": {
    "allowedPaths": "TripDocument",
    "changedPaths": "TripDocument",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/tripState/days/day2/stops/1/description"
  ],
  "serverDocumentChangedPaths": [
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description"
  ],
  "authorizedLegacyPaths": [
    "/days/day2/stops/byId/day2-stop-3/description",
    "/days/day2/stops/byId/day2-stop-3/startTime",
    "/days/day2/stops/byId/day2-stop-2/description",
    "/days/day2/stops/byId/day2-stop-2/startTime"
  ],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 5,
    "parentVersion": 4,
    "baseVersion": 4,
    "candidateId": "p2-ce4ce40209a740819ede905bb2962032",
    "patch": [
      {
        "op": "replace",
        "path": "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
        "value": "沿竹林步道游览，出发前确认开放与入园安排。 在云栖竹径按体力缩短游览，预留20分钟休息；不增加景点。"
      }
    ],
    "changedPaths": [
      "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description"
    ],
    "validationReport": {
      "valid": true,
      "schema": true,
      "scope": true,
      "locks": true,
      "invariants": true,
      "leafDiff": true,
      "legacyBusinessDiff": true
    }
  }
}
```

### 第2轮：明确全局授权：隐藏预算明细

输入：隐藏全局预算展示明细（明确确认）。v5→v6；约束保持：True。

测试预期允许路径：

- `/presentationSettings/showBudgetDetails`

实际投影叶级差异：

- `/presentationSettings/showBudgetDetails`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [],
  "serverAllowedPaths": [
    "/presentationSettings/showBudgetDetails"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/presentationSettings/showBudgetDetails"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 6,
    "parentVersion": 5,
    "baseVersion": 5,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/presentationSettings/showBudgetDetails"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第3轮：第二天卡片精简样式

输入：明确选择第二天整天后精简共享卡片。v6→v7；约束保持：True。

测试预期允许路径：

- `/uiSpec/regions/day-2/compact`

实际投影叶级差异：

- `/uiSpec/regions/day-2/compact`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "card-day-d84f0aeee1a85438a6b2984eabb9442c"
  ],
  "serverAllowedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 7,
    "parentVersion": 6,
    "baseVersion": 6,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/uiSpec/regions/day-2/compact"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第4轮：调整第二天下午开始时间

输入：将选中下午活动开始时间设为14:00。v7→v8；约束保持：True。

测试预期允许路径：

- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`

实际投影叶级差异：

- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`

完整原业务差异：

- `/days/day2/stops/byId/day2-stop-2/startTime`

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "period-day-d84f0aeee1a85438a6b2984eabb9442c-pm"
  ],
  "serverAllowedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "pathNamespaces": {
    "allowedPaths": "TripDocument",
    "changedPaths": "TripDocument",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/tripState/days/day2/stops/1/startTime"
  ],
  "serverDocumentChangedPaths": [
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "authorizedLegacyPaths": [
    "/days/day2/stops/byId/day2-stop-2/startTime"
  ],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 8,
    "parentVersion": 7,
    "baseVersion": 7,
    "candidateId": null,
    "patch": [
      {
        "op": "replace",
        "path": "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime",
        "value": "14:00"
      }
    ],
    "changedPaths": [
      "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
    ],
    "validationReport": {
      "valid": true,
      "schema": true,
      "scope": true,
      "locks": true,
      "invariants": true,
      "leafDiff": true,
      "legacyBusinessDiff": true
    }
  }
}
```

### 第5轮：恢复第二天完整卡片样式

输入：明确选择第二天整天后展开卡片内容。v8→v9；约束保持：True。

测试预期允许路径：

- `/uiSpec/regions/day-2/compact`

实际投影叶级差异：

- `/uiSpec/regions/day-2/compact`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "card-day-d84f0aeee1a85438a6b2984eabb9442c"
  ],
  "serverAllowedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 9,
    "parentVersion": 8,
    "baseVersion": 8,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/uiSpec/regions/day-2/compact"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第6轮：返回第二天下午采用体验方案

输入：安排轻松一些。v9→v10；约束保持：True。

测试预期允许路径：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/description`
- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`

实际投影叶级差异：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`

完整原业务差异：

- `/days/day2/stops/byId/day2-stop-2/description`
- `/days/day2/stops/byId/day2-stop-2/startTime`
- `/days/day2/stops/byId/day2-stop-3/startTime`

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "period-day-d84f0aeee1a85438a6b2984eabb9442c-pm"
  ],
  "serverAllowedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/description",
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "pathNamespaces": {
    "allowedPaths": "TripDocument",
    "changedPaths": "TripDocument",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/tripState/days/day2/stops/1/description",
    "/tripState/days/day2/stops/1/startTime",
    "/tripState/days/day2/stops/2/startTime"
  ],
  "serverDocumentChangedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "authorizedLegacyPaths": [
    "/days/day2/stops/byId/day2-stop-3/description",
    "/days/day2/stops/byId/day2-stop-3/startTime",
    "/days/day2/stops/byId/day2-stop-2/description",
    "/days/day2/stops/byId/day2-stop-2/startTime"
  ],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 10,
    "parentVersion": 9,
    "baseVersion": 9,
    "candidateId": "p2-cfcfeaa1ea8543c9ab8f12ad7eb3b613",
    "patch": [
      {
        "op": "replace",
        "path": "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime",
        "value": "14:15"
      },
      {
        "op": "replace",
        "path": "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
        "value": "16:45"
      },
      {
        "op": "replace",
        "path": "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
        "value": "沿竹林步道游览，出发前确认开放与入园安排。 在云栖竹径按体力缩短游览，预留20分钟休息；不增加景点。 云栖竹径采用慢游节奏，预留40分钟坐下休息；其余景点保持不变。"
      }
    ],
    "changedPaths": [
      "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
      "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
      "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
    ],
    "validationReport": {
      "valid": true,
      "schema": true,
      "scope": true,
      "locks": true,
      "invariants": true,
      "leafDiff": true,
      "legacyBusinessDiff": true
    }
  }
}
```

### 第7轮：明确全局授权：显示预算明细

输入：恢复全局预算展示明细（明确确认）。v10→v11；约束保持：True。

测试预期允许路径：

- `/presentationSettings/showBudgetDetails`

实际投影叶级差异：

- `/presentationSettings/showBudgetDetails`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [],
  "serverAllowedPaths": [
    "/presentationSettings/showBudgetDetails"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/presentationSettings/showBudgetDetails"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 11,
    "parentVersion": 10,
    "baseVersion": 10,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/presentationSettings/showBudgetDetails"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第8轮：第二天局部时间轴

输入：明确选择第二天整天切换可信时间轴。v11→v12；约束保持：True。

测试预期允许路径：

- `/uiSpec/regions/day-2/view`

实际投影叶级差异：

- `/uiSpec/regions/day-2/view`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "card-day-d84f0aeee1a85438a6b2984eabb9442c"
  ],
  "serverAllowedPaths": [
    "/uiSpec/regions/day-2/view"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/uiSpec/regions/day-2/view"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 12,
    "parentVersion": 11,
    "baseVersion": 11,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/uiSpec/regions/day-2/view"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第9轮：明确恢复历史快照并新建版本

输入：恢复v5数据、展示、锁和硬约束（明确确认）。v12→v13；约束保持：True。

测试预期允许路径：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`
- `/uiSpec/regions/day-2/view`

实际投影叶级差异：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`
- `/uiSpec/regions/day-2/view`

完整原业务差异：

- `/days/day2/stops/byId/day2-stop-2/description`
- `/days/day2/stops/byId/day2-stop-2/startTime`
- `/days/day2/stops/byId/day2-stop-3/startTime`

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [],
  "serverAllowedPaths": [
    "/tripState",
    "/uiSpec",
    "/controlState",
    "/routeData",
    "/catalogVersion",
    "/catalog",
    "/planningSettings",
    "/planningWarnings",
    "/editorMetadata",
    "/persistedConstraints",
    "/presentationSettings"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/tripState/days/day2/stops/1/description",
    "/tripState/days/day2/stops/1/startTime",
    "/tripState/days/day2/stops/2/startTime",
    "/uiSpec/regions/day-2/view"
  ],
  "serverDocumentChangedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "authorizedLegacyPaths": [
    "/days/day2/stops/byId/day2-stop-3/startTime",
    "/days/day2/stops/byId/day2-stop-2/description",
    "/days/day2/stops/byId/day2-stop-2/startTime"
  ],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 13,
    "parentVersion": 12,
    "baseVersion": 12,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/tripState/days/day2/stops/1/description",
      "/tripState/days/day2/stops/1/startTime",
      "/tripState/days/day2/stops/2/startTime",
      "/uiSpec/regions/day-2/view"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

### 第10轮：撤销刚才恢复并保留审计链

输入：撤销上一步，恢复其父版本数据及约束。v13→v14；约束保持：True。

测试预期允许路径：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`
- `/uiSpec/regions/day-2/view`

实际投影叶级差异：

- `/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description`
- `/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime`
- `/uiSpec/regions/day-2/view`

完整原业务差异：

- `/days/day2/stops/byId/day2-stop-2/description`
- `/days/day2/stops/byId/day2-stop-2/startTime`
- `/days/day2/stops/byId/day2-stop-3/startTime`

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [],
  "serverAllowedPaths": [
    "/tripState",
    "/uiSpec",
    "/controlState",
    "/routeData",
    "/catalogVersion",
    "/catalog",
    "/planningSettings",
    "/planningWarnings",
    "/editorMetadata",
    "/persistedConstraints",
    "/presentationSettings"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/tripState/days/day2/stops/1/description",
    "/tripState/days/day2/stops/1/startTime",
    "/tripState/days/day2/stops/2/startTime",
    "/uiSpec/regions/day-2/view"
  ],
  "serverDocumentChangedPaths": [
    "/entities/activities/activity-3fc5637492cc5e4098f429373998af07/startTime",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/description",
    "/entities/activities/activity-749f005a92fb57aba9d026c7097ac6d2/startTime"
  ],
  "authorizedLegacyPaths": [
    "/days/day2/stops/byId/day2-stop-3/startTime",
    "/days/day2/stops/byId/day2-stop-2/description",
    "/days/day2/stops/byId/day2-stop-2/startTime"
  ],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 14,
    "parentVersion": 13,
    "baseVersion": 13,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/tripState/days/day2/stops/1/description",
      "/tripState/days/day2/stops/1/startTime",
      "/tripState/days/day2/stops/2/startTime",
      "/uiSpec/regions/day-2/view"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action"
    }
  }
}
```

### 第11轮：另一客户端明确修改第二天样式以形成冲突

输入：另一客户端明确选择第二天并精简卡片。v14→v15；约束保持：True。

测试预期允许路径：

- `/uiSpec/regions/day-2/compact`

实际投影叶级差异：

- `/uiSpec/regions/day-2/compact`

完整原业务差异：

- 无

服务端范围、路径命名空间、实际payload差异、约束与候选审计：

```json
{
  "selectedComponentIds": [
    "card-day-d84f0aeee1a85438a6b2984eabb9442c"
  ],
  "serverAllowedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "pathNamespaces": {
    "allowedPaths": "payloadSnapshot",
    "changedPaths": "payloadSnapshot",
    "payloadChangedPaths": "payloadSnapshot",
    "documentChangedPaths": "TripDocument"
  },
  "serverPayloadChangedPaths": [
    "/uiSpec/regions/day-2/compact"
  ],
  "serverDocumentChangedPaths": [],
  "authorizedLegacyPaths": [],
  "unauthorizedLegacyPaths": [],
  "constraintsSnapshot": {
    "lockedComponentIds": [
      "card-day-7b33a86ff2aa50a595c53b4e78832637",
      "card-day-ab4129b6002458a69c53d74abfb6de1f"
    ],
    "hardConstraints": [
      {
        "id": "time-order",
        "kind": "time_order",
        "active": true
      },
      {
        "id": "max-budget",
        "kind": "max_budget",
        "active": true,
        "value": 3000
      }
    ]
  },
  "historyRecord": {
    "tripId": "df45e8430d2b48008ffa0e02acf8297e",
    "version": 15,
    "parentVersion": 14,
    "baseVersion": 14,
    "candidateId": null,
    "patch": [],
    "changedPaths": [
      "/uiSpec/regions/day-2/compact"
    ],
    "validationReport": {
      "valid": true,
      "source": "trusted_user_action",
      "invariants": true
    }
  }
}
```

## 4. 拒绝、失败原子性与重启

| 用例 | HTTP | 正式状态 |
| --- | --- | --- |
| Locked day display edit | 423 | 保持不变 |
| Old candidate cannot overwrite new version | 409 | 保持不变 |
| Ordinary budget edit cannot break saved cap | 400 | 保持不变 |
| Visible candidate conflict identifies scope and both versions | 409 | 保持不变 |
| Missing explicit global consent | 422 | 保持不变 |
| Restart invalidates preview but not versions | 410 | 保持不变 |

旧候选错误带expectedVersion和currentVersion，UI同时显示当前选区；409不会自动应用或合并旧Patch。候选重启后仅内存预览失效，410不影响已保存版本。

本次真正停止/重启隔离后台：PID 25796 → 13456；actualRestart=True，versionsAndConstraintsEqual=True。重启前后完整正式Session、SQLite行及版本列表相等；刷新后继续行程也一致。地图回归随后一次明确查询形成额外版本，其无Key降级状态不计入前10轮主序列。

## 5. 视觉与几何：独立于业务数据指标

固定1600×1100、DPR1、同一浏览器和字体加载完成；浏览模式裁剪第一天/第三天首个景点卡片及酒店。裁剪无动态时钟、地图瓦片或远程图片，没有使用掩膜隐藏待测内容。11轮×3个组件共33对裁剪比较；本次最终运行累计原始差异像素0，显著差异像素0（各对详情保存在JSON，重复像素按不同轮次分别计）。

容差明示：裁剪尺寸相同；任一RGB通道差>2算显著像素，保护卡片显著像素必须为0；原始差异比例须≤0.01%。保留原始PNG、rawDifferentPixels、rawFraction、maxChannelDelta和boundingBox，不能把容差通过写成严格像素相同。酒店裁剪差异照实记录，主动测量其几何位置以观察间接漂移。

| 采样 | 第二天面板高度(px) | 酒店内容Y(px) | 预算内容Y(px) | 与上一轮几何变化 |
| --- | --- | --- | --- | --- |
| baseline | 1250.2969 | 1555.2969 | 1555.2969 | 0项 |
| round-01 | 1250.2969 | 1555.2969 | 1555.2969 | 0项 |
| round-02 | 1250.2969 | 1555.2969 | 1555.2969 | 0项 |
| round-03 | 1114.7344 | 1419.7344 | 1419.7344 | 3项 |
| round-04 | 1114.7344 | 1419.7344 | 1419.7344 | 0项 |
| round-05 | 1250.2969 | 1555.2969 | 1555.2969 | 3项 |
| round-06 | 1272.4844 | 1577.4844 | 1577.4844 | 3项 |
| round-07 | 1272.4844 | 1577.4844 | 1577.4844 | 0项 |
| round-08 | 1287.4844 | 1592.4844 | 1592.4844 | 3项 |
| round-09 | 1250.2969 | 1555.2969 | 1555.2969 | 3项 |
| round-10 | 1287.4844 | 1592.4844 | 1592.4844 | 3项 |
| round-11 | 1174.7344 | 1479.7344 | 1479.7344 | 3项 |

归因：第3轮compact压缩第二天卡片高度，第5轮展开恢复；第6轮描述增加造成换行，第8轮时间轴改变布局，第9/10轮恢复/撤销重现相应几何；第11轮其他客户端精简卡片再次改变高度。酒店/预算的业务字段没有因此改写，但它们在同列中的内容Y位置真实移动。这些归为**间接布局变化**，与**直接未授权数据变化=0**分别记录，不将它们合并成一个保真分数。

几何只采样第二天激活时的hotel-card、budget-card、day-panel，第一/三天是首卡局部裁剪，不能据此声称所有非目标组件绝对位置、整页像素或所有视口都不变。表中精确数值及原始DOM记录可在JSON核对。

## 6. 首轮视觉失败与局限

最终复跑首次在第6轮候选已成功提交后的visualSample精确零差断言失败；失败时该轮尚未写入rounds数组，所以失败报告只列出前5轮。对应API提交并未失败，视觉检查前的全原业务Diff、锁、约束与快照断言已经通过。第一天870×278裁剪有8个像素各RGB通道最多相差1，坐标集中于文字/链接边缘，bbox=[114,95,189,253]。这与字边缘抗锯齿舍入噪声一致，为有证据支持的推断，不能证明唯一原因；原失败未单独采集保护卡片绝对位置，不能声称该位置已证实完全不变。

证据：[首轮失败日志](verification/phase3-final-browser-first.log)、[失败JSON](verification/phase3-browser-failure-first.json)、[8像素逐点诊断](verification/phase3-visual-retry-diagnosis.json)。随后加入上述有限视觉阈值并保留raw数据，业务/锁/约束/几何检查没有放宽；重新运行整条序列通过。截图检查不等同于语义、地图可用性或所有功能都正确。

## 7. 测试组与截图

| 实际E2E组 | 结果 |
| --- | --- |
| Explicit component locks and budget hard cap are versioned; baseline snapshot created | 通过 |
| Candidate switching and cancel preserve official Session and SQLite bytes | 通过 |
| Ten successful serial edits preserve locked and unauthorized business leaves and constraints | 通过 |
| Actual UI stale-candidate commit explains scope and old/current versions without overwriting newer data | 通过 |
| Refresh and actual server restart preserve formal versions, snapshots and constraints | 通过 |
| Original map fallback, source links, navigation and history controls remain usable | 通过 |
| Mobile history remains readable without horizontal overflow or browser errors | 通过 |

- [重启后的持久版本历史](screenshots/phase3/history-after-restart.png)
- [手机历史列表](screenshots/phase3/mobile-history.png)
- [第一天基线裁剪](screenshots/phase3/baseline-day1.png)
- [第三天第10轮裁剪](screenshots/phase3/round-10-day3.png)
- [第3轮酒店裁剪](screenshots/phase3/round-03-hotel.png)

全部轮次截图：`docs/screenshots/phase3/baseline-{day1,day3,hotel}.png`及`round-01`至`round-11`对应三组件PNG。没有录屏。最终截图覆盖7个测试组、手机390×844无横向溢出；地图仅验证未配置Key的降级、503与安全链接，没有真实地图瓦片或路线查询成功证明。

## 8. 复现

在依赖齐全的仓库根目录运行：

```powershell
pnpm build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-phase3-tests.ps1
node tests/trip-schema.cjs
node tests/phase2-schema.cjs
node tests/phase3-ui.cjs
```

Phase3脚本自管8014和UUID隔离库，遇到端口已占用会停止而不结束未知进程；脚本退出停止其自有服务，不操作8011正式服务。需要Playwright、Edge与Pillow（本机使用已带运行库；UI_BROWSER_PATH/PLAYWRIGHT_MODULE_DIR/UI_IMAGE_PYTHON可指定）。LLM与地图Key明确置空，测试不读取正式.env。完整交付及修改清单见[Phase 3验收](phase3-acceptance.md)。未进入Phase 4。
