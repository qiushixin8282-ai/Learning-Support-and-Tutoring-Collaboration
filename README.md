# Learning Support and Tutoring Collaboration

一个可运行的训练原型：把学习支持请求变成**受控、可追溯、人工确认**的服务任务。它面向“北辰在线学院”的合成训练场景，不连接真实学生、课程或生产系统。

## 为什么做

当前支持请求常通过多渠道进入，助教、课程运营和讲师之间的交接缺少统一版本与责任记录。原型把每个请求标准化为任务，并在缺少授权、规则版本或人工确认时停止自动流转，转交具名负责人。

## 快速开始

```bash
python -m venv .venv
.venv\\Scripts\\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m learning_support_collab.main --demo
python -m learning_support_collab.main --lifecycle-demo
python -m unittest discover -s tests -v
```

## 项目结构

- `src/learning_support_collab/`：领域模型、规则和 CLI。
- `config/default.yaml`：非机密默认规则。
- `data/sample_requests.json`：匿名合成样例，不含真实个人资料。
- `docs/`：设计、接口、FDE 全流程交付与履历资料位。
- `tests/`：核心规则和回归测试。

## 当前边界

- 仅演示任务分流与审计记录，不发送消息、不修改课程或学生系统。
- 没有授权、规则版本或责任人时，结果为 `needs_human_review`。
- 指标、ROI 与试点结论均为待验证的训练假设，不能当作生产收益。

详见 [设计说明](docs/design.md)、[接口说明](docs/api.md)、[FDE 交付包](docs/fde-delivery.md)、[项目推荐书](docs/项目推荐书.md)、[现场调研与需求定义包](docs/现场调研与需求定义包.md)、[行业分析报告](docs/学习支持与辅导协作行业分析报告.docx)、[现状流程图](docs/现状流程图.svg)、[转型后流程图](docs/数智化转型后流程图.svg)、[设计方案](docs/设计方案.md) 和 [报价单](docs/报价单.md)。

面向下一阶段系统建设的范围与架构建议见 [学习支持协作实验室一期需求清单](docs/学习支持协作实验室一期需求清单.md)。
