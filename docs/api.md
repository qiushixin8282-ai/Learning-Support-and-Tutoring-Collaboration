# 接口说明

## `evaluate_request`

```python
evaluate_request(request: SupportRequest, *, required_rule_version: str) -> Evaluation
```

输入 `SupportRequest`：

| 字段 | 含义 | 必填 |
| --- | --- | --- |
| `request_id` | 去标识化请求编号 | 是 |
| `learner_region` | 服务适用区域 | 是 |
| `topic` | 请求主题 | 是 |
| `rule_version` | 已核对规则版本 | 是 |
| `consent_confirmed` | 是否已确认授权 | 是 |
| `requester_role` | 提出请求的角色 | 是 |
| `assigned_owner` | 具名处理责任人 | 是 |

输出 `Evaluation`：`decision`、`reasons`、`next_owner`、`audit_events`。当控制条件任一不满足，`decision` 必为 `needs_human_review`。
