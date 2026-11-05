"""学习支持协作实验室的 FastAPI 服务入口。

本文件实现第一批可运行能力：SQLite 初始化、JWT 登录、角色权限、
服务端渲染后台主页、提示词版本管理和审计日志查询。
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.dependencies import current_user, require_admin
from app.models.schemas import LoginRequest, LoginResponse, Role, UserView
from app.services.audit import new_trace_id, write_audit_log
from app.services.database import connection, initialize_database
from app.services.security import create_access_token, verify_password


ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT / "admin" / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """在服务启动时创建最小表结构；不自动创建弱口令账号。"""

    initialize_database()
    yield


app = FastAPI(title="学习支持协作实验室", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "admin" / "static")), name="static")


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
        conn.execute(
            "INSERT INTO knowledge_items(title, source_type, visibility, content, created_by_user_id) VALUES (?, ?, ?, ?, ?)",
            (title, source_type, visibility, content, user.id),
        )
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value,
                    event_type="knowledge_created", result_status="success", detail=f"新增资料：{title}")
    return RedirectResponse(url="/admin/knowledge?success=资料已加入知识库", status_code=303)


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
