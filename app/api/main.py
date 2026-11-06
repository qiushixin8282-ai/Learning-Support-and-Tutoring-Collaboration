"""学习支持协作实验室的 FastAPI 服务入口。

本文件实现第一批可运行能力：SQLite 初始化、JWT 登录、角色权限、
服务端渲染后台主页、提示词版本管理和审计日志查询。
"""

from __future__ import annotations

import sqlite3
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.dependencies import current_user, require_admin
from app.api.task_router import router as task_router
from app.models.schemas import LoginRequest, LoginResponse, Role, UserView
from app.services.audit import new_trace_id, write_audit_log
from app.services.database import connection, initialize_database
from app.services.document_parser import DocumentParseError, parse_and_store
from app.services.rag_service import generate_answer, index_knowledge_item, model_config, retrieve, save_deepseek_config
from app.services.security import create_access_token, hash_password, password_error, verify_password


ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "uploads" / "knowledge"
templates = Jinja2Templates(directory=str(ROOT / "admin" / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """在服务启动时创建最小表结构；不自动创建弱口令账号。"""

    initialize_database()
    yield


app = FastAPI(title="学习支持协作实验室", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "admin" / "static")), name="static")
app.include_router(task_router)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """所有 JSON 接口的业务错误使用统一中文响应格式。"""

    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def authenticate(email: str, password: str) -> UserView | None:
    """校验账号密码并转换为不会携带密码字段的用户视图。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, password_hash, role, is_active FROM users WHERE email = ?", (email,)
        ).fetchone()
    if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
        return None
    return UserView(id=row["id"], email=row["email"], display_name=row["display_name"], role=Role(row["role"]))


def login_response(user: UserView) -> JSONResponse:
    """签发 JWT 并放进 HttpOnly Cookie，页面和 API 均可复用。"""

    token = create_access_token(user.id, user.role)
    response = JSONResponse(content=LoginResponse(access_token=token, user=user).model_dump(mode="json"))
    response.set_cookie("access_token", token, httponly=True, samesite="lax", secure=False, max_age=8 * 60 * 60)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """供部署平台与 CI 使用的最小健康检查。"""

    return {"status": "ok"}


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> JSONResponse:
    """JSON 登录接口；失败时不泄露账号是否存在。"""

    user = authenticate(payload.email, payload.password)
    trace_id = new_trace_id()
    if not user:
        write_audit_log(trace_id=trace_id, user_id=None, role=None, event_type="login", result_status="failed", detail="账号或密码错误")
        return error_response(status.HTTP_401_UNAUTHORIZED, "LOGIN_FAILED", "账号或密码错误")
    write_audit_log(trace_id=trace_id, user_id=user.id, role=user.role.value, event_type="login", result_status="success", detail="登录成功")
    return login_response(user)


@app.post("/login")
def login_form(email: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    """服务端渲染登录表单的提交入口。"""

    user = authenticate(email, password)
    trace_id = new_trace_id()
    if not user:
        write_audit_log(trace_id=trace_id, user_id=None, role=None, event_type="login", result_status="failed", detail="账号或密码错误")
        return RedirectResponse(url="/login?error=账号或密码错误", status_code=status.HTTP_303_SEE_OTHER)
    write_audit_log(trace_id=trace_id, user_id=user.id, role=user.role.value, event_type="login", result_status="success", detail="登录成功")
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", create_access_token(user.id, user.role), httponly=True, samesite="lax", secure=False, max_age=8 * 60 * 60)
    return response


@app.post("/api/auth/logout")
def logout(user: UserView = Depends(current_user)) -> RedirectResponse:
    """删除浏览器会话 Cookie 并记录退出事件。"""

    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="logout", result_status="success", detail="用户退出登录")
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@app.get("/api/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)) -> UserView:
    return user


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.get("/login")
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.get("/admin")
def admin_dashboard(request: Request, user: UserView = Depends(require_admin)):
    """仅管理员可访问的 SaaS 风格后台概览。"""

    with connection() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        knowledge_count = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE status = '已发布'").fetchone()[0]
        task_count = conn.execute("SELECT COUNT(*) FROM support_tasks WHERE status NOT IN ('已完成', '已归档')").fetchone()[0]
        prompt = conn.execute("SELECT name, version, updated_at FROM prompt_templates WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
        recent_logs = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 6").fetchall()
        task_stats = conn.execute("SELECT status, COUNT(*) AS total FROM support_tasks GROUP BY status").fetchall()
        recent_tasks = conn.execute("SELECT * FROM support_tasks WHERE status NOT IN ('已完成','已归档') ORDER BY CASE priority WHEN '紧急' THEN 1 WHEN '重要' THEN 2 ELSE 3 END, id DESC LIMIT 4").fetchall()
        recent_knowledge = conn.execute("SELECT * FROM knowledge_items ORDER BY id DESC LIMIT 4").fetchall()
        activity_days = conn.execute(
            "SELECT substr(created_at, 6, 5) AS day, COUNT(*) AS total FROM audit_logs "
            "WHERE created_at >= datetime('now', '-6 days') GROUP BY date(created_at) ORDER BY date(created_at)"
        ).fetchall()
        completed_count = conn.execute("SELECT COUNT(*) FROM support_tasks WHERE status = '已完成'").fetchone()[0]
        all_task_count = conn.execute("SELECT COUNT(*) FROM support_tasks WHERE status != '已归档'").fetchone()[0]
        completion_rate = round(completed_count * 100 / all_task_count) if all_task_count else 0
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "user_count": user_count, "audit_count": audit_count,
         "knowledge_count": knowledge_count, "task_count": task_count, "prompt": prompt,
         "recent_logs": recent_logs, "task_stats": task_stats, "recent_tasks": recent_tasks,
         "recent_knowledge": recent_knowledge, "activity_days": activity_days,
         "completion_rate": completion_rate},
    )


@app.get("/admin/prompts")
def prompts_page(request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        prompt = conn.execute("SELECT * FROM prompt_templates WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
        history = conn.execute("SELECT * FROM prompt_templates ORDER BY version DESC LIMIT 10").fetchall()
    return templates.TemplateResponse(request, "prompts.html", {"user": user, "prompt": prompt, "history": history})


@app.post("/admin/prompts")
def update_prompt(content: str = Form(...), user: UserView = Depends(require_admin)) -> RedirectResponse:
    """管理员发布新提示词版本；问答服务后续应只读取 is_active=1 的版本。"""

    content = content.strip()
    if not content:
        return RedirectResponse(url="/admin/prompts?error=提示词不能为空", status_code=status.HTTP_303_SEE_OTHER)
    with connection() as conn:
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_templates").fetchone()[0]
        conn.execute("UPDATE prompt_templates SET is_active = 0 WHERE is_active = 1")
        conn.execute(
            "INSERT INTO prompt_templates(name, content, version, is_active, updated_by_user_id) VALUES (?, ?, ?, 1, ?)",
            ("默认资料问答提示词", content, version, user.id),
        )
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="prompt_updated", result_status="success", detail=f"发布提示词版本 {version}")
    return RedirectResponse(url="/admin/prompts?success=提示词已发布", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/audit-logs")
def audit_logs_page(
    request: Request, role: str | None = None, event_type: str | None = None,
    user: UserView = Depends(require_admin),
):
    """按角色和事件类型过滤审计日志，展示脱敏后的摘要。"""

    sql = "SELECT * FROM audit_logs WHERE 1 = 1"
    values: list[str] = []
    if role:
        sql += " AND role = ?"
        values.append(role)
    if event_type:
        sql += " AND event_type = ?"
        values.append(event_type)
    sql += " ORDER BY id DESC LIMIT 100"
    with connection() as conn:
        logs = conn.execute(sql, values).fetchall()
    return templates.TemplateResponse(request, "audit_logs.html", {"user": user, "logs": logs, "selected_role": role or "", "selected_event": event_type or ""})


@app.get("/admin/knowledge")
def knowledge_page(request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        items = conn.execute("SELECT * FROM knowledge_items ORDER BY id DESC").fetchall()
    return templates.TemplateResponse(request, "knowledge.html", {"user": user, "items": items})


@app.post("/admin/knowledge")
def create_knowledge(
    title: str = Form(...), source_type: str = Form(...), visibility: str = Form(...),
    content: str = Form(...), user: UserView = Depends(require_admin),
) -> RedirectResponse:
    title, content = title.strip(), content.strip()
    if not title or not content:
        return RedirectResponse(url="/admin/knowledge?error=标题和正文不能为空", status_code=303)
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO knowledge_items(title, source_type, visibility, content, created_by_user_id) VALUES (?, ?, ?, ?, ?)",
            (title, source_type, visibility, content, user.id),
        )
        item_id = cursor.lastrowid
    try:
        chunk_count = index_knowledge_item(item_id)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(url=f"/admin/knowledge?{urlencode({'error': f'资料已保存，但向量索引失败：{exc}'})}", status_code=303)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="knowledge_created", result_status="success", detail=f"新增资料：{title}")
    return RedirectResponse(url=f"/admin/knowledge?success=资料已发布并生成 {chunk_count} 个检索切片", status_code=303)


@app.post("/admin/knowledge/upload")
async def upload_knowledge(
    document: UploadFile = File(...), visibility: str = Form("全员可见"),
    user: UserView = Depends(require_admin),
) -> RedirectResponse:
    """校验、保存并解析 PDF/Markdown/TXT，成功后发布到知识库。"""
    allowed_visibility = {"全员可见", "助教及以上", "导师及以上", "仅管理员"}
    if visibility not in allowed_visibility:
        return RedirectResponse(url="/admin/knowledge?error=不支持的可见范围", status_code=303)
    try:
        parsed = await parse_and_store(document, UPLOAD_DIR)
    except DocumentParseError as exc:
        write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                        event_type="knowledge_upload_failed", result_status="failed", detail=str(exc), risk_flag="file_validation")
        return RedirectResponse(url=f"/admin/knowledge?{urlencode({'error': str(exc)})}", status_code=303)
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO knowledge_items(
                title, source_type, visibility, content, status, created_by_user_id,
                original_name, stored_name, mime_type, size_bytes, page_count, char_count, parse_status
            ) VALUES (?, ?, ?, ?, '已发布', ?, ?, ?, ?, ?, ?, ?, '已解析')""",
            (Path(parsed.original_name).stem, parsed.source_type, visibility, parsed.content, user.id,
             parsed.original_name, parsed.stored_name, parsed.mime_type, parsed.size_bytes,
             parsed.page_count, parsed.char_count),
        )
        item_id = cursor.lastrowid
    try:
        chunk_count = index_knowledge_item(item_id)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(url=f"/admin/knowledge?{urlencode({'error': f'文件已解析，但向量索引失败：{exc}'})}", status_code=303)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="knowledge_uploaded", result_status="success",
                    detail=f"上传并解析：{parsed.original_name}（{parsed.char_count} 字符）")
    return RedirectResponse(url=f"/admin/knowledge?success=文件已解析并生成 {chunk_count} 个检索切片", status_code=303)


ATLAS_TEMPLATE = """# Atlas 世界模型知识卡

## 基本信息
Atlas 是 World Labs 于 2026 年 9 月发布的下一代空间智能世界模型。World Labs 由李飞飞等人创立，目标是构建能够感知、生成、推理并与三维世界交互的世界模型。

## 核心定位
Atlas 不是单一的图片或视频生成器，而是一个统一的世界建模基础模型。它从头预训练，原生处理文本、图像、视频、相机位姿与三维深度等信息，并把输入组织成共享的空间上下文。

## 模型架构
Atlas 被描述为多模态自回归扩散 Transformer。不同模态输入被定位到三维空间中，模型随后基于空间上下文逐步生成新的多模态内容，以保持视角、几何结构和时间演化的一致性。

## 主要能力
1. 相机可控生成：根据一张或多张参考图，从指定相机位置与角度生成新视图和长视频。
2. 空间重建：从少量或多张图像恢复真实场景，生成新视角图像与显式三维输出。
3. 时空模拟：从视频理解空间与时间变化，可用于视频重构以及机器人 Real-to-Sim 工作流。
4. 图像与全景生成：根据文本生成图像和 360 度全景，并支持复杂提示与多种视觉风格。

## 与 Marble 的关系
Marble 是 World Labs 已公开的多模态世界生成产品，可从文本、图像、视频或粗略三维布局创建可编辑的三维世界。Atlas 将为 Marble 的未来版本及 World Labs 的其他产品提供基础能力。

## 学习讨论问题
- 世界模型与大语言模型的上下文分别表示什么？
- 三维空间一致性为什么比单帧视觉质量更难？
- 从真实场景重建到机器人仿真，需要控制哪些误差？
- 世界模型可能如何改变影视、游戏、建筑、教育和机器人研究？

来源：World Labs, “Atlas: A World Model for Spatial Intelligence”, 2026-09-01, https://www.worldlabs.ai/blog/atlas
"""


@app.get("/admin/rag")
def rag_page(request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        chunk_count = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(DISTINCT knowledge_item_id) FROM knowledge_chunks").fetchone()[0]
        recent = conn.execute("SELECT * FROM rag_queries ORDER BY id DESC LIMIT 8").fetchall()
        prompt = conn.execute("SELECT content FROM prompt_templates WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    return templates.TemplateResponse(request, "rag.html", {
        "user": user, "chunk_count": chunk_count, "item_count": item_count, "recent": recent,
        "config": model_config(), "prompt": prompt["content"] if prompt else "只依据资料回答。",
        "question": "", "answer": None, "hits": [],
    })


@app.post("/admin/rag/ask")
def rag_ask(request: Request, question: str = Form(...), user: UserView = Depends(require_admin)):
    question = question.strip()
    if not question:
        return RedirectResponse(url="/admin/rag?error=问题不能为空", status_code=303)
    with connection() as conn:
        prompt = conn.execute("SELECT content FROM prompt_templates WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    try:
        hits = retrieve(question, user.role.value)
        answer, mode, model_name = generate_answer(question, hits, prompt["content"] if prompt else "只依据资料回答。")
    except RuntimeError as exc:
        return RedirectResponse(url=f"/admin/rag?{urlencode({'error': str(exc)})}", status_code=303)
    with connection() as conn:
        conn.execute(
            "INSERT INTO rag_queries(user_id, question, answer, source_ids, model_name, mode) VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, question, answer, json.dumps([hit.chunk_id for hit in hits]), model_name, mode),
        )
        chunk_count = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(DISTINCT knowledge_item_id) FROM knowledge_chunks").fetchone()[0]
        recent = conn.execute("SELECT * FROM rag_queries ORDER BY id DESC LIMIT 8").fetchall()
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="rag_question", result_status="success", detail=f"RAG 问答：命中 {len(hits)} 个切片")
    return templates.TemplateResponse(request, "rag.html", {
        "user": user, "chunk_count": chunk_count, "item_count": item_count, "recent": recent,
        "config": model_config(), "prompt": prompt["content"] if prompt else "只依据资料回答。",
        "question": question, "answer": answer, "hits": hits,
    })


@app.post("/admin/rag/settings/deepseek")
def rag_deepseek_settings(
    api_key: str = Form(...), model: str = Form("deepseek-v4-flash"),
    user: UserView = Depends(require_admin),
) -> RedirectResponse:
    api_key = api_key.strip()
    if not api_key:
        return RedirectResponse(url="/admin/rag?error=请输入 DeepSeek API Key", status_code=303)
    save_deepseek_config(api_key, model)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="model_configured", result_status="success", detail=f"已连接 DeepSeek：{model}")
    return RedirectResponse(url="/admin/rag?success=DeepSeek 模型连接已保存，可以开始真实 RAG 问答", status_code=303)


@app.get("/admin/rag/connect")
def rag_connect_page(request: Request, user: UserView = Depends(require_admin)):
    return templates.TemplateResponse(request, "rag_connect.html", {"user": user, "config": model_config()})


@app.post("/admin/rag/reindex")
def rag_reindex(user: UserView = Depends(require_admin)) -> RedirectResponse:
    with connection() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM knowledge_items WHERE status = '已发布'").fetchall()]
    total, errors = 0, []
    for item_id in ids:
        try:
            total += index_knowledge_item(item_id)
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    if errors:
        return RedirectResponse(url=f"/admin/rag?{urlencode({'error': f'部分索引失败：{errors[0]}'})}", status_code=303)
    return RedirectResponse(url=f"/admin/rag?success=索引重建完成，共 {total} 个切片", status_code=303)


@app.post("/admin/rag/templates/atlas")
def create_atlas_template(user: UserView = Depends(require_admin)) -> RedirectResponse:
    with connection() as conn:
        existing = conn.execute("SELECT id FROM knowledge_items WHERE title = ?", ("Atlas 世界模型知识卡",)).fetchone()
        if existing:
            item_id = existing["id"]
            conn.execute("UPDATE knowledge_items SET content = ?, status = '已发布', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ATLAS_TEMPLATE, item_id))
        else:
            cursor = conn.execute(
                "INSERT INTO knowledge_items(title, source_type, visibility, content, status, created_by_user_id) VALUES (?, '知识模板', '全员可见', ?, '已发布', ?)",
                ("Atlas 世界模型知识卡", ATLAS_TEMPLATE, user.id),
            )
            item_id = cursor.lastrowid
    try:
        count = index_knowledge_item(item_id)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(url=f"/admin/rag?{urlencode({'error': f'模板已保存，但索引失败：{exc}'})}", status_code=303)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="rag_template_created", result_status="success", detail="创建 Atlas 世界模型知识模板")
    return RedirectResponse(url=f"/admin/rag?success=Atlas 模板已创建并生成 {count} 个切片", status_code=303)


@app.get("/admin/knowledge/{item_id}")
def knowledge_detail(item_id: int, request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        item = conn.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="资料不存在")
    return templates.TemplateResponse(request, "knowledge_detail.html", {"user": user, "item": item})


@app.get("/admin/knowledge/{item_id}/download")
def download_knowledge(item_id: int, user: UserView = Depends(require_admin)) -> FileResponse:
    with connection() as conn:
        item = conn.execute("SELECT original_name, stored_name, mime_type FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    if not item or not item["stored_name"]:
        raise HTTPException(status_code=404, detail="原始文件不存在")
    path = (UPLOAD_DIR / item["stored_name"]).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="原始文件不存在")
    return FileResponse(path, media_type=item["mime_type"] or "application/octet-stream", filename=item["original_name"])


@app.post("/admin/knowledge/{item_id}/toggle")
def toggle_knowledge(item_id: int, user: UserView = Depends(require_admin)) -> RedirectResponse:
    with connection() as conn:
        item = conn.execute("SELECT title, status FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="资料不存在")
        new_status = "已停用" if item["status"] == "已发布" else "已发布"
        conn.execute("UPDATE knowledge_items SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, item_id))
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="knowledge_status_changed", result_status="success", detail=f"{item['title']} → {new_status}")
    return RedirectResponse(url="/admin/knowledge?success=资料状态已更新", status_code=303)


@app.get("/admin/tasks")
def tasks_page(request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        tasks = conn.execute("SELECT * FROM support_tasks ORDER BY CASE priority WHEN '紧急' THEN 1 WHEN '重要' THEN 2 ELSE 3 END, id DESC").fetchall()
    return templates.TemplateResponse(request, "tasks.html", {"user": user, "tasks": tasks})


@app.post("/admin/tasks")
def create_task(
    title: str = Form(...), description: str = Form(""), owner: str = Form(...),
    priority: str = Form("普通"), due_date: str = Form(""), user: UserView = Depends(require_admin),
) -> RedirectResponse:
    with connection() as conn:
        conn.execute(
            "INSERT INTO support_tasks(title, description, owner, priority, due_date, created_by_user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (title.strip(), description.strip(), owner.strip(), priority, due_date or None, user.id),
        )
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="task_created", result_status="success", detail=f"创建任务：{title.strip()}")
    return RedirectResponse(url="/admin/tasks?success=协作任务已创建", status_code=303)


@app.post("/admin/tasks/{task_id}/status")
def update_task_status(task_id: int, task_status: str = Form(...), user: UserView = Depends(require_admin)) -> RedirectResponse:
    allowed = {"待分配", "进行中", "待审核", "已完成", "已归档"}
    if task_status not in allowed:
        raise HTTPException(status_code=400, detail="不支持的任务状态")
    with connection() as conn:
        task = conn.execute("SELECT title FROM support_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        conn.execute("UPDATE support_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_status, task_id))
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="task_status_changed", result_status="success", detail=f"{task['title']} → {task_status}")
    return RedirectResponse(url="/admin/tasks?success=任务状态已更新", status_code=303)


@app.get("/admin/users")
def users_page(request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        users = conn.execute("SELECT id, email, display_name, role, is_active, created_at FROM users ORDER BY id").fetchall()
    return templates.TemplateResponse(request, "users.html", {"user": user, "users": users})


@app.post("/admin/users/{user_id}/toggle")
def toggle_user(user_id: int, user: UserView = Depends(require_admin)) -> RedirectResponse:
    if user_id == user.id:
        return RedirectResponse(url="/admin/users?error=不能停用当前登录账号", status_code=303)
    with connection() as conn:
        target = conn.execute("SELECT email, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        new_value = 0 if target["is_active"] else 1
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_value, user_id))
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="user_status_changed", result_status="success", detail=f"{target['email']} → {'启用' if new_value else '停用'}")
    return RedirectResponse(url="/admin/users?success=账号状态已更新", status_code=303)


@app.get("/admin/users/new")
def new_user_page(request: Request, user: UserView = Depends(require_admin)):
    return templates.TemplateResponse(request, "user_form.html", {"user": user, "roles": [role.value for role in Role]})


@app.post("/admin/users/new")
def create_user(
    email: str = Form(...), display_name: str = Form(...), role: str = Form(...),
    password: str = Form(...), confirm_password: str = Form(...),
    user: UserView = Depends(require_admin),
) -> RedirectResponse:
    email, display_name = email.strip().lower(), display_name.strip()
    if not email or "@" not in email:
        return RedirectResponse(url="/admin/users/new?error=请输入有效邮箱", status_code=303)
    if not display_name:
        return RedirectResponse(url="/admin/users/new?error=请输入成员姓名", status_code=303)
    if role not in {item.value for item in Role}:
        return RedirectResponse(url="/admin/users/new?error=角色无效", status_code=303)
    if password != confirm_password:
        return RedirectResponse(url="/admin/users/new?error=两次输入的密码不一致", status_code=303)
    if error := password_error(password):
        return RedirectResponse(url=f"/admin/users/new?{urlencode({'error': error})}", status_code=303)
    try:
        with connection() as conn:
            conn.execute("INSERT INTO users(email, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
                         (email, display_name, hash_password(password), role))
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/admin/users/new?error=该邮箱已存在", status_code=303)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="user_created", result_status="success", detail=f"创建账号 {email}，角色 {role}")
    return RedirectResponse(url="/admin/users?success=新账号已创建", status_code=303)


@app.get("/admin/users/{user_id}/password")
def reset_password_page(user_id: int, request: Request, user: UserView = Depends(require_admin)):
    with connection() as conn:
        target = conn.execute("SELECT id, email, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    return templates.TemplateResponse(request, "reset_password.html", {"user": user, "target": target})


@app.post("/admin/users/{user_id}/password")
def reset_password(
    user_id: int, password: str = Form(...), confirm_password: str = Form(...),
    user: UserView = Depends(require_admin),
) -> RedirectResponse:
    if password != confirm_password:
        return RedirectResponse(url=f"/admin/users/{user_id}/password?error=两次输入的密码不一致", status_code=303)
    if error := password_error(password):
        return RedirectResponse(url=f"/admin/users/{user_id}/password?{urlencode({'error': error})}", status_code=303)
    with connection() as conn:
        target = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="password_reset", result_status="success", detail=f"管理员重置 {target['email']} 的密码")
    return RedirectResponse(url="/admin/users?success=密码已重置", status_code=303)


@app.get("/account/password")
def change_password_page(request: Request, user: UserView = Depends(current_user)):
    return templates.TemplateResponse(request, "change_password.html", {"user": user})


@app.post("/account/password")
def change_password(
    current_password: str = Form(...), password: str = Form(...), confirm_password: str = Form(...),
    user: UserView = Depends(current_user),
) -> RedirectResponse:
    with connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if not row or not verify_password(current_password, row["password_hash"]):
        return RedirectResponse(url="/account/password?error=当前密码不正确", status_code=303)
    if password != confirm_password:
        return RedirectResponse(url="/account/password?error=两次输入的新密码不一致", status_code=303)
    if verify_password(password, row["password_hash"]):
        return RedirectResponse(url="/account/password?error=新密码不能与当前密码相同", status_code=303)
    if error := password_error(password):
        return RedirectResponse(url=f"/account/password?{urlencode({'error': error})}", status_code=303)
    with connection() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user.id))
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="password_changed", result_status="success", detail="用户修改自己的密码")
    response = RedirectResponse(url="/login?success=密码修改成功，请重新登录", status_code=303)
    response.delete_cookie("access_token")
    return response
