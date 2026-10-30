# 学习支持协作实验室：Agent 执行循环框架设计

> 版本：v0.1  
> 阶段：代码实现前的架构确认稿

## 1. 框架选择

### 推荐：自建受控 Harness（轻量执行循环）

采用项目内的轻量 `AgentHarness` 作为唯一的流程控制器，并通过 `LLMAdapter` 适配 OpenAI 兼容接口或后续供应商。Harness 自己维护“模型决策 → 工具执行 → 工具结果回填 → 下一轮决策”的最多五轮循环。

选择原因：

1. **权限与审批边界最清晰**：正式状态流转工具根本不注册给模型；草稿写入在 Harness 层被拦截，进入人工确认状态。
2. **供应商可替换**：工具 JSON Schema、消息格式和 `LLMAdapter` 独立于具体 SDK，可沿用现有环境变量方式接入 OpenAI 兼容服务。
3. **审计完整**：Harness 在工具调用的唯一入口记录每轮决策，避免日志分散在框架回调中。
4. **可控终止**：业务定义的五轮上限、中文错误码、停止条件均由本项目代码强制执行。
5. **适合当前规模**：一期只需一个受控学习支持 Agent，不应过早拆分多智能体。

### 备选一：OpenAI Agents SDK

若后续确定长期只使用 OpenAI，可用 Agents SDK 承担模型与工具循环；它支持函数工具、运行状态和人工审批恢复。仍应保留本项目的权限检查、前置校验、草稿拦截和业务审计层，不能把这些责任交给 SDK。官方说明中，SDK 适合由应用自行拥有部署、工具实现、状态存储和审批决策的场景。

### 备选二：LangGraph

若未来需要跨天恢复审批、复杂分支、多角色协作或多 Agent 编排，可将本 Harness 的状态机迁移为 LangGraph 图。当前阶段不采用，避免增加图编排、检查点和模型封装依赖。

## 2. 核心模块职责

```text
FastAPI / CLI
  → AgentHarness.run()
      → PolicyGuard（角色、资源范围、工具白名单）
      → LLMAdapter.decide()（模型生成工具调用或最终答案）
      → ToolRegistry.execute()（参数与前置状态校验）
      → ApprovalGate（草稿写入前中断）
      → AgentDecisionLogRepository（每轮审计）
  → SSE / 普通响应
```

| 模块 | 职责 |
| --- | --- |
| `AgentHarness` | 控制最多五轮循环、终止、回填消息和追踪 ID。 |
| `LLMAdapter` | 统一不同 LLM 供应商的工具调用请求与响应格式。 |
| `PolicyGuard` | 根据 JWT 用户、角色及“看/改/审/发布”动作筛掉不可用工具。 |
| `ToolRegistry` | 为工具定义 JSON Schema，并统一执行参数、权限与业务规则校验。 |
| `ApprovalGate` | 识别草稿写入工具，先向前端流式发送草稿和确认令牌，停止本轮运行。 |
| `AgentDecisionLogRepository` | 将每轮决策和工具结果写入日志表。 |

## 3. Agent 状态

```text
RUNNING
  ├─ 模型输出最终答案 → COMPLETED
  ├─ 模型请求只读工具 → 执行工具并进入下一轮
  ├─ 模型请求草稿写入工具 → WAITING_USER_CONFIRMATION
  ├─ 工具返回业务错误 → 将中文错误回填模型，进入下一轮或完成
  ├─ 权限校验失败 → REJECTED
  └─ 达到第 5 轮仍未完成 → MAX_ROUNDS_EXCEEDED

WAITING_USER_CONFIRMATION
  ├─ 用户确认且令牌有效 → 执行草稿写入 → COMPLETED
  └─ 用户取消、超时或令牌无效 → CANCELLED
```

`MAX_ROUNDS_EXCEEDED` 面向用户的固定提示为：

> 当前问题需要的处理步骤较多，已为保护数据安全停止自动处理。请缩小问题范围，或由人工继续处理。

## 4. 单轮执行算法

每轮按以下顺序执行，`round_no` 从 1 到 5：

1. 读取会话上下文、用户身份、角色和可用工具白名单。
2. 调用模型，请模型输出最终回答或严格符合 JSON Schema 的工具调用。
3. 对工具名、参数类型和用户权限进行代码层校验。
4. 执行只读工具，或执行仅写草稿区的准备类工具。
5. 将工具结果（包括错误码、中文错误信息和来源）回填到模型消息历史。
6. 在 `agent_decision_logs` 写入本轮日志。
7. 若为草稿写入请求，则由 `ApprovalGate` 中断，向前端 SSE 输出草稿预览与确认令牌；不写入草稿。
8. 若模型已给出无工具调用的最终回答，则统一补充来源标注并结束。

## 5. 草稿确认与正式操作边界

### 5.1 创建成果提交草稿

```text
模型请求 create_submission_draft
  → ToolRegistry 调用 validate_submission_requirements
  → 校验不通过：返回错误码和中文缺失项，不创建草稿
  → 校验通过：生成不可复用、短时有效的 approval_token
  → SSE 推送 draft_preview + approval_required
  → Agent 状态置为 WAITING_USER_CONFIRMATION
  → 用户点击“确认保存草稿”
  → 后端再次校验 token、JWT、用户、任务和权限
  → 写入草稿记录并记录审计
```

草稿预览可流式发送，但在用户确认前不得写入数据库中的草稿记录。确认令牌绑定 `trace_id`、`user_id`、`task_id`、草稿内容摘要和过期时间，防止重放或篡改。

### 5.2 明确禁止的智能体动作

`submit_for_review`、`approve_review`、`reject_review`、`mark_final`、`publish_result` 不加入 `ToolRegistry` 的 Agent 可用工具集合。它们只允许由人工页面调用后端接口，并由服务端复核 JWT、角色权限、当前状态和 CSRF 保护。

## 6. 工具前置校验

| 工具 | 强制前置条件 | 未满足时返回 |
| --- | --- | --- |
| `search_knowledge` | 用户存在且角色在资源可见范围内 | `KNOWLEDGE_ACCESS_DENIED`：无权访问该资料 |
| `create_task_draft` | 具备任务“改”权限 | `TASK_EDIT_FORBIDDEN`：无创建任务草稿权限 |
| `create_submission_draft` | 校验令牌有效、任务归属正确、必填关联完整 | `SUBMISSION_VALIDATION_REQUIRED`：成果提交信息未校验或不完整 |
| `request_human_review` | 草稿存在且归属正确 | `DRAFT_NOT_FOUND`：未找到可提交的成果草稿 |

工具失败并不由模型自行解释为成功；Harness 会把错误码和中文信息作为工具结果回填，模型只能据此引导用户补充材料。

## 7. 每轮决策日志表

建议新增 `agent_decision_logs`：

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `trace_id` | 一次用户请求的全链路 ID |
| `session_id` | 会话 ID |
| `round_no` | 第几轮，限制 1 至 5 |
| `tool_name` | 工具名；最终回答轮记为 `final_answer` |
| `tool_parameters_json` | 脱敏后的参数 JSON |
| `duration_ms` | 从调用到拿到结果的耗时 |
| `result_status` | `success`、`business_error`、`denied`、`awaiting_confirmation`、`failed` |
| `result_summary` | 脱敏结果摘要，不保存大段资料正文 |
| `actor_user_id` | 操作用户 ID |
| `actor_role` | 当时操作角色 |
| `source_chunk_ids_json` | 使用的资料切片 ID 列表 |
| `created_at` | 写入时间 |

所有日志与已有问答审计日志以 `trace_id` 关联；后续可据此分析工具调用频率、平均耗时、失败原因和风险事件。

## 8. 典型对话预期轨迹

### a. “这门课的实验报告要求是什么”

```text
第 1 轮：模型 → search_knowledge(query=实验报告要求)
第 1 轮：工具 → 仅返回当前角色可见资料切片与来源
第 2 轮：模型 → answer_with_sources
结束：返回实验报告要求 + 文件名和切片序号
```

不调用任何写入类工具；若无匹配资料，固定答复“资料中未找到”。

### b. “帮我提交上周的实验成果”

```text
第 1 轮：模型 → 检索用户待提交任务
第 2 轮：模型 → validate_submission_requirements
第 2 轮：工具 → 缺少实验记录，返回 SUBMISSION_VALIDATION_REQUIRED
第 3 轮：模型 → 告知用户需要补充实验记录，结束
```

若所有材料齐全：

```text
第 1 轮：检索任务
第 2 轮：校验必填项、实验记录、代码链接与 commit
第 3 轮：生成草稿预览与 approval_token，状态变为 WAITING_USER_CONFIRMATION
人工确认：服务端再次校验后保存草稿
```

智能体不会提交审核，用户需后续人工点击“提交审核”。

### c. “这个任务有没有风险”

```text
第 1 轮：模型 → check_task_risks(task_id)
第 1 轮：工具 → 返回临期、实验记录缺失、代码链接缺失等风险和依据
第 2 轮：模型 → 整理风险结论、优先级和补救建议
结束：附任务字段、状态或资料来源作为依据
```

## 9. 实施边界

第一版仅实现一个学习支持 Agent，五轮循环、工具白名单、人工草稿确认、SQLite 日志和 SSE 事件。多 Agent 编排、异步任务队列、跨天恢复和可视化指标面板留待后续阶段。
