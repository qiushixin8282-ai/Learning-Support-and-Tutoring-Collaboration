# 学习支持协作实验室

面向学生、助教、导师与课题组负责人的受控协作实验室训练项目。项目将学习任务、资料检索、实验记录、成果版本、审核留痕与 AI 建议统一到可追溯的协作流程中。

> 本仓库只包含自编的匿名示例资料和训练原型；不连接真实学生、课程、实验或生产系统。

## 核心功能

- 受控任务流转：关键步骤需要授权、规则版本与具名责任人。
- 权限感知资料问答原型：先按角色过滤资料，再执行 BM25 与向量混合检索。
- 可追溯成果链路：任务、资料/实验记录、代码 commit、成果版本与审核记录可关联。
- Agent 治理设计：工具白名单、五轮执行上限、前置校验、草稿人工确认和全链路审计。
- 测试与评测资产：包含任务流转单元测试及 30 条 Agent 评测用例。

## 架构图（文字版）

```text
入口层 app/api/
  └─ 命令行入口；后续承载 FastAPI 路由、SSE 与鉴权依赖
服务层 app/services/
  └─ 任务流转、规则校验、资料检索、权限过滤、审计能力
智能体编排层 app/agents/
  └─ 工具注册、受控执行循环、人工确认中断、模型供应商适配
数据模型层 app/models/
  └─ 领域对象；后续扩展 SQLite/PostgreSQL 表结构与 Schema

管理后台 admin/
  └─ 后续服务端渲染页面、模板与静态资源
```

权限校验在 API 依赖和服务工具内部双重执行；审计日志由服务层与 Agent Harness 统一写入。AI 只能提出建议或生成待确认草稿，不能自行提交审核、定稿或发布。

## 目录说明

```text
.
├── app/
│   ├── api/          # 入口层：CLI、后续路由与 SSE
│   ├── services/     # 服务层：流转、检索、权限与审计
│   ├── agents/       # 智能体编排层预留
│   ├── models/       # 领域模型与后续 Schema
│   └── config/       # 非机密默认配置
├── admin/            # 管理后台模板与静态资源预留
├── tests/            # 单元测试与 Agent 评测用例
├── docs/             # 架构、接口、流程图、交付与上线检查文档
├── data/             # 仅自编、匿名、可公开的示例资料
├── scripts/          # 本地构建和部署提示脚本
├── .env.example      # 环境变量名称模板
├── requirements.txt
├── pyproject.toml
├── LICENSE
└── README.md
```

## 安装与运行

要求：Python 3.11+。

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -e .

# 运行匿名任务分流演示
python -m app.api.cli --demo

# 运行可审计任务生命周期演示
python -m app.api.cli --lifecycle-demo

# 运行测试
python -m unittest discover -s tests -v
```

## 管理后台原型

第一批 FastAPI 管理后台已实现 JWT 登录、五类角色的“看/改/审/发布”动作模型、SQLite 初始化、系统提示词版本管理和审计日志查询。

```bash
# 首次本地运行：交互式创建系统管理员（密码不会写入代码）
python -m app.api.bootstrap --email admin@example.com --name "系统管理员"

# 启动服务后访问 http://127.0.0.1:8000/login
uvicorn app.api.main:app --reload
```

生产部署前必须设置 `APP_ENV=production` 和高强度 `JWT_SECRET`，并通过反向代理启用 HTTPS；资料上传、向量检索和 Agent 执行循环将在后续批次接入。

## 资料问答原型

阶段 1 原型使用本地 Markdown、角色权限过滤、FAISS 与 BM25 混合检索。运行前复制环境变量模板并填写自己的密钥；不要提交 `.env` 文件。

```bash
copy .env.example .env
# 填写 LLM_MODEL 与 LLM_API_KEY 后运行
python -m app.services.rag_prototype --document data/阶段1学习支持资料.md --role 学生 --question "学生可以提交什么内容？"
```

原型回答会标注“文件名 + 切片序号”；检索不到时返回“资料中未找到”。生成的 JSONL 审计日志、FAISS 索引和本地数据库均被 `.gitignore` 排除。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `LLM_PROVIDER` | LLM 供应商适配类型，当前原型为 `openai_compatible`。 |
| `LLM_MODEL` | 模型名称。 |
| `LLM_API_KEY` | 模型 API Key，仅通过本地环境变量提供。 |
| `LLM_BASE_URL` | OpenAI 兼容 API 地址。 |
| `JWT_SECRET` | 后续 Web 登录鉴权使用的服务端密钥。 |
| `DATABASE_URL` | 后续 Web 服务使用的数据库连接地址。 |

## 演示与文档

- [项目设计方案](docs/设计方案.md)
- [Python 后端技术架构](docs/Python后端技术架构方案.md)
- [用户端问答接口设计](docs/用户端问答接口设计.md)
- [智能体工具与治理设计](docs/智能体工具与治理设计.md)
- [Agent 执行循环框架设计](docs/Agent执行循环框架设计.md)
- [30 条 Agent 评测用例](tests/agent_evaluation_cases.json)
- [上线前检查清单](docs/上线前检查清单.md)
- [部署与公开仓库说明](docs/部署与公开仓库说明.md)
- [现状流程图](docs/学习支持协作全流程现状图.svg) 与 [数智化转型流程图](docs/学习支持协作数智化转型全流程图.svg)

## 当前边界

本仓库的可运行部分是任务流转 CLI 和阶段 1 RAG 原型。FastAPI 问答入口、服务端渲染管理后台、SQLite 会话、Agent 执行循环与人工审核页面已完成架构、接口、评测及上线检查设计，后续可按文档分阶段实现。

公开仓库的安全扫描、提交规范、创建方式和未登录复核步骤见 [部署与公开仓库说明](docs/部署与公开仓库说明.md)。
