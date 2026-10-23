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

## `transition`

```python
transition(task: SupportTask, target: TaskStatus, *, actor: str, reason: str = "") -> SupportTask
```

支持的任务流转为 `received → assigned → drafted → under_review → published → handed_off`。复核不通过时只能进入 `returned_for_revision`，再回到 `drafted`；不允许跳过复核直接发布。每次流转都记录操作人、原因和前后状态。
