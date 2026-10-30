# 学习支持协作实验室 Python 后端技术架构方案

## 四层架构

```text
入口层
Web 前端 管理后台 REST API OAuth2 登录 GitHub或Gitee链接录入
请求进入 身份认证 项目上下文 动作权限预检

服务层 FastAPI
用户与角色 项目与成员 任务 资料 实验记录 成果版本 审核
代码链接 知识库 指标 审计服务
所有写操作在此层执行看 改 审 发布权限校验

智能体编排层
检索权限过滤 RAG检索 LLM供应商适配器 来源引用
摘要 学习路径建议 风险提醒 AI会话
AI仅生成建议草稿 不直接修改正式记录

数据模型层
PostgreSQL加pgvector 对象存储 审计日志 任务状态事件
SQLite仅用于本地开发或第一周验证
```

权限校验落在服务层：每个资源操作均验证项目成员关系、角色和动作权限。审计日志由服务层统一写入，智能体编排层补充模型、检索来源和输出摘要。

## 关键选型

- FastAPI：支持异步 I/O，适合数据库、文件、向量检索与外部 LLM API 调用。
- PostgreSQL 与 pgvector：一期将业务数据和向量检索放在同一数据库，降低运维复杂度。
- 对象存储：附件与成果文件保存在对象存储，数据库仅保存元数据、版本和访问控制信息。
- LLM Provider Adapter：统一摘要、检索、学习路径建议和风险提醒接口，便于替换供应商。
- 审计日志：记录成功、拒绝、异常、AI 调用与人工采纳行为。

## 推荐目录结构

```text
learning-support-lab/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── deps.py
│   │   └── v1/
│   │       ├── auth.py
│   │       ├── projects.py
│   │       ├── tasks.py
│   │       ├── materials.py
│   │       ├── experiments.py
│   │       ├── outcomes.py
│   │       ├── reviews.py
│   │       ├── knowledge_base.py
│   │       └── ai.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── permissions.py
│   │   ├── audit.py
│   │   └── exceptions.py
│   ├── domain/
│   ├── services/
│   ├── agents/
│   ├── db/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── migrations/
│   │   └── session.py
│   └── schemas/
├── tests/
├── scripts/
├── docs/
├── alembic.ini
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

## 数据库设计

| 表名 | 核心字段 | 关系 |
| --- | --- | --- |
| `users` | `id`, `email`, `password_hash`, `display_name`, `status` | 用户主体 |
| `roles` | `id`, `code`, `name` | 学生、助教、导师、负责人、管理员 |
| `permissions` | `id`, `resource_type`, `action` | 动作：`view/edit/review/publish` |
| `role_permissions` | `role_id`, `permission_id` | 角色默认权限 |
| `projects` | `id`, `name`, `description`, `owner_id`, `status` | 实验室项目或课题 |
| `project_members` | `project_id`, `user_id`, `role_id`, `scope` | 项目成员与权限边界 |
| `tasks` | `id`, `project_id`, `parent_task_id`, `title`, `status`, `owner_id` | 任务主线 |
| `task_events` | `task_id`, `from_status`, `to_status`, `actor_id`, `reason`, `occurred_at` | 状态审计与指标计算 |
| `materials` | `project_id`, `task_id`, `title`, `object_key`, `visibility`, `version` | 项目或任务资料 |
| `experiment_records` | `project_id`, `task_id`, `method`, `result_summary`, `status`, `author_id` | 实验过程记录 |
| `code_references` | `task_id`, `provider`, `repository_url`, `branch`, `commit_hash` | GitHub或Gitee代码引用 |
| `outcomes` | `project_id`, `source_task_id`, `title`, `status`, `current_version_id` | 成果与来源任务关联 |
| `outcome_versions` | `outcome_id`, `version_no`, `content_ref`, `created_by`, `status`, `parent_version_id` | 成果版本链 |
| `outcome_sources` | `outcome_version_id`, `source_type`, `source_id`, `citation_note` | 来源追溯 |
| `review_records` | `outcome_version_id`, `reviewer_id`, `decision`, `comment`, `evidence_ref` | 人工审核记录 |
| `knowledge_documents` | `project_id`, `material_id`, `title`, `visibility`, `index_status` | 授权知识库资料 |
| `knowledge_chunks` | `document_id`, `chunk_index`, `content`, `embedding`, `source_locator` | pgvector 文本切片 |
| `ai_sessions` | `project_id`, `user_id`, `purpose`, `provider`, `model` | AI会话 |
| `ai_messages` | `session_id`, `role`, `content`, `source_refs`, `is_suggestion` | AI建议与来源 |
| `ai_runs` | `session_id`, `input_scope`, `retrieval_query`, `model`, `output_hash`, `status` | AI调用审计 |
| `audit_logs` | `actor_id`, `project_id`, `action`, `resource_type`, `resource_id`, `result`, `metadata` | 统一审计日志 |

核心关系：

```text
projects ──< project_members >── users
projects ──< tasks ──< task_events
tasks ──< materials / experiment_records / code_references
tasks ──< outcomes ──< outcome_versions ──< review_records
outcome_versions ──< outcome_sources >── tasks/materials/experiment_records/code_references
knowledge_documents ──< knowledge_chunks
ai_sessions ──< ai_messages / ai_runs
```

## 状态流转

任务流转：

```text
new → assigned → in_progress → submitted → completed → archived
                    ↑              │
                    └── returned ──┘
```

- 助教、导师、负责人、管理员可分派任务。
- 负责人可开始任务并提交。
- 导师、负责人或具备审核权限者可通过或退回任务。
- 负责人、管理员可归档完成任务。

成果版本流转：

```text
draft → submitted_for_review → approved → published
  ↑              │
  └── revision_required ──────────────┘
```

- 学生、助教和任务负责人可创建或修改草稿。
- 导师、负责人可审核并通过或退回修订。
- 仅负责人或具备发布权限者可发布。
- 发布后创建新版本，不覆盖原版本。

指标规则：

- 处理时长：完成时间减创建时间，必要时扣除暂停时间。
- 返工次数：进入 `returned` 或 `revision_required` 的次数。
- 版本正确率：首次审核通过且无版本错误的成果数除以已审核成果数。
- 实验复现状态：`not_tested`、`reproducible`、`partially_reproducible`、`not_reproducible`；必须记录判定人、时间、环境、commit hash 和依据。

## 第一周最小验证目标

跑通：

```text
创建任务 → 指定负责人 → 提交成果 v1 → 导师人工审核 → 通过或退回修订 → 留痕
```

AI 验证点：

```text
选择已授权资料 → 向量检索 → AI生成摘要 → 返回来源与定位 → 写入审计日志
```

验收标准：

- 可创建项目、两名用户和一项任务。
- 学生仅能访问被授权任务，不能审核或发布。
- 导师可通过或退回成果 v1，且必须填写审核意见。
- 每次状态变化均记录操作人、时间、前后状态和理由。
- 成果版本可追溯至来源任务，至少关联一份资料或实验记录。
- AI 摘要显示至少一个来源文档及片段定位。
- AI 不可直接修改任务、实验记录、成果或审核结果。

## 主要风险与规避

| 风险 | 规避措施 |
| --- | --- |
| 权限泄露 | 每次查询按项目成员与动作权限过滤；知识库先进行权限过滤再向量检索；下载与导出行为审计。 |
| AI 越权或幻觉 | AI 仅返回建议，禁止直接调用正式写接口；输出强制带来源；未检索到来源时明确提示不确定。 |
| 追溯断链 | 成果版本必须关联来源任务；审核记录关联成果版本；代码引用保存仓库链接与 commit hash。 |
| 资料与个人信息泄露 | 使用私有对象存储、短期签名访问和项目级隔离；限制文件类型与大小；建立留存和删除策略。 |
| 指标失真 | 指标由状态事件计算；禁止手工改写关键事件；复现状态必须记录判定人和依据。 |
