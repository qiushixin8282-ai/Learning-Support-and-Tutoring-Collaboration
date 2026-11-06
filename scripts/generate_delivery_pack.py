"""Generate the assessment delivery pack from the repository's training materials."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "交付包"
OUT.mkdir(parents=True, exist_ok=True)

INTERVIEWS = [
    ("学习服务主任", "顾明岚", "需要把继续试点的决定、停止条件和价值口径写清。", "每周复盘一次，缺少统一时长和返工指标。"),
    ("助教", "林亦舟", "资料散在群文件、共享盘和代码仓库，版本核对靠人工。", "每周约3次查找资料，每次约25分钟。"),
    ("课程运营员", "唐映秋", "交接时常缺规则版本和具名责任人，需反复补问。", "每周约2次，每次约20分钟。"),
    ("课程讲师", "叶书", "专业结论和特殊学习支持必须由讲师最终判断。", "审核意见应包含依据、修改项和验收条件。"),
    ("教育数据专员", "傅景", "资料可见范围、脱敏、留存与审计尚待具名确认。", "未授权资料不可进入学生检索范围。"),
    ("平台工程师", "秦芮", "现有系统需提供状态变化、责任人、版本与异常日志。", "先做单课程受控试点，再验证接口与回滚。"),
]

SCENARIOS = {
    "正常场景测试": ("助教创建控制项齐全的任务，补齐实验记录与 commit 后提交导师审核，导师确认可复现并通过。", ["创建任务", "已分派", "进行中", "待审核", "导师通过", "已完成"], "任务创建 P95 < 1秒；流程6步均有审计；版本正确率100%；复现状态为可复现。"),
    "边界场景测试": ("对空标题、超长说明、规则版本为空、缺少来源资料、缺少实验记录或 commit 的任务逐项验证。", ["输入校验", "创建新建任务", "尝试分派", "阻断或转人工", "记录原因"], "6类边界样本100%返回可读错误；不产生错误分派；审计原因完整率100%。"),
    "对抗场景测试": ("提交提示词注入、伪造导师身份、资料内嵌越权指令和无来源问题，验证系统只信任服务端角色与授权资料。", ["攻击输入", "权限过滤", "检索约束", "拒绝越权", "风险审计"], "4类攻击样本0条越权数据泄露；0次正式状态改变；风险事件记录率100%。"),
    "权限场景测试": ("学生、助教、导师和负责人分别请求创建、流转、审核、指标接口，并查询不同可见范围资料。", ["签发角色令牌", "调用接口", "服务端鉴权", "返回允许或403", "审计"], "未授权写入100%返回403；导师可审核；学生不可创建；不可见资料检索结果为0。"),
    "故障场景测试": ("模拟模型服务超时、空知识库、向量维度不一致和数据库暂时不可用，验证降级与错误信息。", ["故障注入", "请求执行", "超时或异常", "降级响应", "恢复与审计"], "故障不产生半截结论；无命中固定提示；错误不泄露密钥；恢复后健康检查为ok。"),
}


def shade(cell, color: str):
    tc_pr = cell._tc.get_or_add_tcPr(); fill = OxmlElement("w:shd"); fill.set(qn("w:fill"), color); tc_pr.append(fill)


def set_cell(cell, text: str, header=False):
    cell.text = text; cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for p in cell.paragraphs:
        for r in p.runs:
            r.font.name = "Microsoft YaHei"; r.font.size = Pt(9); r.font.color.rgb = RGBColor(255,255,255) if header else RGBColor(0,0,0)
    if header: shade(cell, "1F4E78")


def add_table(doc, headers, rows):
    table = doc.add_table(rows=1, cols=len(headers)); table.style = "Table Grid"; table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for c, value in zip(table.rows[0].cells, headers): set_cell(c, value, True)
    for row in rows:
        cells = table.add_row().cells
        for c, value in zip(cells, row): set_cell(c, str(value))
    doc.add_paragraph()


def setup(title: str, subtitle: str):
    doc = Document(); section = doc.sections[0]; section.top_margin = Inches(.7); section.bottom_margin = Inches(.7)
    style = doc.styles["Normal"]; style.font.name = "Microsoft YaHei"; style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei"); style.font.size = Pt(10)
    p = doc.add_paragraph(style="Title"); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; r = p.add_run(title); r.font.name = "Microsoft YaHei"; r._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei"); r.font.color.rgb = RGBColor(0,0,0)
    p = doc.add_paragraph(subtitle); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return doc


def heading(doc, text):
    p = doc.add_paragraph(style="Heading 1"); r = p.add_run(text); r.font.name = "Microsoft YaHei"; r._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei"); r.font.color.rgb = RGBColor(0,0,0)


def flow_image(name, steps):
    path = OUT / f"{name}.png"; width, height = 1500, 260; image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    n = len(steps); box = 210; gap = (width - 80 - box*n) // max(n-1, 1)
    for i, step in enumerate(steps):
        x = 40 + i*(box+gap); draw.rounded_rectangle((x,70,x+box,175), 16, fill="#DCEAF7", outline="#1F4E78", width=3)
        draw.multiline_text((x+18,104), step, font=font, fill="#102A43", spacing=4)
        if i < n-1: draw.polygon([(x+box+15,120),(x+box+45,120),(x+box+45,108),(x+box+70,132),(x+box+45,156),(x+box+45,144),(x+box+15,144)], fill="#1F4E78")
    image.save(path); return path


def save(doc, name): doc.save(OUT / f"{name}.docx")


def write_interviews():
    records = [{"访谈编号": f"IV-{i:02d}", "角色": r, "化名": n, "核心陈述": q, "量化线索": m, "证据状态": "教学演练样例（虚构，待真实访谈替换）"} for i,(r,n,q,m) in enumerate(INTERVIEWS,1)]
    (OUT / "六人访谈记录.md").write_text("# 六人访谈记录\n\n> 教学演练样例（虚构），用于需求调研方法演示，不代表真实客户访谈。\n\n" + "\n".join(f"## {x['访谈编号']} {x['角色']}\n- 核心陈述：{x['核心陈述']}\n- 量化线索：{x['量化线索']}" for x in records), encoding="utf-8")
    (OUT / "六人访谈记录.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "六人访谈记录.csv").open("w", encoding="utf-8-sig", newline="") as f: csv.DictWriter(f, fieldnames=records[0].keys()).writeheader(); csv.DictWriter(f, fieldnames=records[0].keys()).writerows(records)
    doc = setup("六人访谈记录整理", "教学演练样例  用于需求调研与试点准备")
    doc.add_paragraph("本记录将角色陈述、量化线索和待验证事项分离。所有内容均为教学演练样例，真实立项前须以脱敏样本、制度规则和系统日志交叉核验。")
    add_table(doc, ["编号", "角色", "核心陈述", "量化线索"], [[x["访谈编号"],x["角色"],x["核心陈述"],x["量化线索"]] for x in records]); save(doc, "六人访谈记录整理")


def write_event():
    text = """典型事件采访记录（教学演练样例，虚构）\n\n采访者：需求分析师\n受访者：学生 小周（化名）\n主题：学习行为预测模型 v2.3 成果提交与审核\n\n第1轮\n问：你从领取任务到首次提交，最费时间的地方是什么？\n答：10月14日09:25开始找代码、数据划分和实验记录。共享盘有7个相似目录，38分钟才确认版本；结果偏低后又看了16条消息、4个提交记录、3份文档，排查52分钟。\n\n第2轮\n问：提交和审核发生了什么？\n答：成果共传了3次。第一次缺随机种子，第二次图表版本错。助教给了5条意见，其中可复现性说明不清，我追问3次才知道要补环境、随机种子、数据版本和运行命令。\n\n第3轮\n问：最终返工和定稿情况？\n答：报告commit与实际运行代码不一致，重跑关键实验多等1小时45分钟。导师又要分组分析，最终返工2轮。任务历时57.5小时，实际投入10小时45分钟，审核等待21小时25分钟。\n"""
    (OUT / "典型事件采访记录.txt").write_text(text, encoding="utf-8")
    doc = setup("典型事件记录整理", "模型复现实验提交与审核  教学演练样例")
    doc.add_paragraph("结论：版本来源分散、提交前检查缺失和审核意见不结构化，导致重复查找、重复上传和返工。该案例为虚构教学样例，数字仅可作为试点指标口径示范。")
    add_table(doc,["阶段","量化观察","可改造点"],[["资料查找","38分钟确认版本，52分钟排查","授权检索与版本来源卡"],["提交","3次上传","必填项与commit一致性检查"],["审核","5条意见，追问3次","结构化审核意见草稿"],["返工","2轮，额外等待1小时45分钟","风险提醒与复现证据模板"]]); save(doc,"典型事件记录整理")


def write_reports():
    reports = [
        ("行业分析", "行业分析", "学习支持协作面临资料分散、责任交接不透明、版本核对依赖经验和审核难复盘等问题。受控协作平台的价值不在替代导师判断，而在统一任务、资料、证据、审核和审计链路。", [["趋势","AI检索和摘要","仅在授权资料范围内辅助"],["风险","数据权限与错误结论","RBAC、人工确认、审计"],["机会","减少查找和返工","以处理时长、返工、版本正确率验证"]]),
        ("需求定义", "需求分析方案", "一期采用单实验室受控试点。需求必须能回溯到典型事件、角色访谈、制度规则或系统日志；目前演练材料中的数字均待真实样本验证。", [["RQ-01","版本化资料检索","来源、版本、可见角色可追溯"],["RQ-02","受控任务流转","授权、规则版本、责任人缺一不可"],["RQ-03","审核与返工","审核意见、返工次数、复现状态留痕"],["RQ-04","AI辅助","仅建议和草稿，人工确认后写正式记录"]]),
        ("详细项目设计方案", "详细项目设计方案", "系统由任务协作、知识库问答、审核复现、审计治理和指标看板组成。三处人工确认点为异常分派、审核决定和复现判定；至少两类权限为学生只读与导师审核权限。", [["架构","FastAPI SQLite FAISS","单课程MVP，后续可迁移PostgreSQL"],["人工确认","异常分派 审核 复现","禁止AI直接批准或发布"],["权限","学生 助教 导师 负责人 管理员","服务端JWT与检索过滤双重校验"]]),
        ("报价与投入产出", "报价与投入产出测算", "原型训练交付参考价为9万元。生产部署、云资源和持续运维不在原报价中；低中高三档用于预算讨论，必须以试点业务量、采用率和真实工时成本复核。", [["低","一次性11万元 月运行0.31万元","假设，小规模试点"],["中","一次性12.5万元 月运行0.60万元","假设，80任务每月"],["高","一次性15万元 月运行0.70万元","假设，规模化使用"],["收益口径","处理时长 返工 版本正确率 复现状态","真实基线待试点建立"]]),
        ("上线交付运行手册", "项目上线交付运行手册", "上线前必须完成权限、审计、备份、回滚、关键流程和异常场景验证。当前交付为MVP训练原型；生产上线需由业务、技术和数据风险责任人共同签收。", [["启动","配置环境变量并初始化数据库","不提交密钥或真实敏感数据"],["日常运营","维护资料版本、任务责任人、审核记录","每周复核异常与未结项"],["故障","模型不可用时降级为检索结果","记录trace并通知管理员"],["回滚","回退应用与索引快照","验证健康检查和核心数据"]]),
    ]
    for filename,title,intro,rows in reports:
        if filename == "行业分析": (OUT / "行业分析文档.md").write_text(f"# {title}\n\n{intro}\n", encoding="utf-8")
        doc=setup(title,"学习支持协作实验室 MVP 交付文档"); doc.add_paragraph(intro); heading(doc,"核心内容"); add_table(doc,["主题","说明","控制或验收"],rows); heading(doc,"范围与限制"); doc.add_paragraph("本文为项目训练与受控试点交付。涉及实际个人信息、正式研究结论、生产发布和成本收益的数据必须经过具名责任人确认。") ; save(doc,filename)


def write_test_docs():
    plan = setup("MVP 系统测试方案", "正常 边界 对抗 权限 故障 五类量化测试")
    plan.add_paragraph("目标是在受控试点前验证任务闭环、权限边界、AI降级和审计可追溯性。每类测试均保留输入、预期、实际、trace_id、截图和结论。")
    add_table(plan,["类别","样本数","核心指标","通过标准"],[[k,5,"状态正确率、错误码、审计完整率",v[2]] for k,v in SCENARIOS.items()]); heading(plan,"执行规则"); plan.add_paragraph("所有写操作使用独立测试数据库。任何未授权写入、不可见资料泄露、AI自动变更正式状态或无审计事件均为阻断级缺陷。")
    save(plan,"测试方案")
    (OUT / "测试方案.md").write_text("# MVP 系统测试方案\n\n覆盖正常、边界、对抗、权限和故障场景。每类5个样本，记录输入、输出、状态、错误码、审计事件与截图。\n\n"+"\n".join(f"## {k}\n- 过程：{' → '.join(v[1])}\n- 通过：{v[2]}" for k,v in SCENARIOS.items()),encoding="utf-8")
    for name,(desc,steps,criterion) in SCENARIOS.items():
        image=flow_image(name,steps); doc=setup(name,"可视化测试过程文档")
        doc.add_paragraph("测试目的："+desc); heading(doc,"测试过程图"); doc.add_picture(str(image),width=Inches(6.7)); heading(doc,"量化测试用例")
        add_table(doc,["编号","输入或动作","预期结果","证据"],[[f"{name[:2]}-{i}",step,"状态、错误码或审计符合预期","截图、响应体、trace_id"] for i,step in enumerate(steps,1)])
        heading(doc,"通过标准"); doc.add_paragraph(criterion); heading(doc,"结果记录"); add_table(doc,["执行日期","执行人","通过数/总数","缺陷编号","复核人"],[["待执行","待指定","0/5","无","待指定"]]); save(doc,name)


def write_deployment():
    doc=setup("MVP 系统上线部署地址", "部署地址与验收记录")
    doc.add_paragraph("当前 GitHub 仓库已保存可运行 MVP 源码和 CI 配置。由于本项目尚未获得云账号、域名、数据处理审批和生产密钥授权，不能将训练原型表述为已生产上线。")
    add_table(doc,["项目","地址或状态","说明"],[["源码仓库","https://github.com/qiushixin8282-ai/Learning-Support-and-Tutoring-Collaboration","已推送的版本化源码"],["MVP 生产网站","待部署","需获得云资源、HTTPS域名、数据与安全审批后发布"],["本地健康检查","http://127.0.0.1:8000/health","本机启动后返回状态ok"]])
    heading(doc,"部署完成后验收"); doc.add_paragraph("记录正式 HTTPS 地址、部署版本号、健康检查截图、角色权限测试结果、回滚版本和具名批准人。未完成这些项目不得对外宣称生产上线。") ; save(doc,"MVP系统上线部署地址")


def main():
    write_interviews(); write_event(); write_reports(); write_test_docs(); write_deployment()


if __name__ == "__main__": main()
