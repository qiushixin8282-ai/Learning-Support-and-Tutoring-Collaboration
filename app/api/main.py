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
def logout(user: UserView = Depends(current_user)) -> JSONResponse:
    """删除浏览器会话 Cookie 并记录退出事件。"""

    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="logout", result_status="success", detail="用户退出登录")
    response = JSONResponse(content={"message": "已退出登录"})
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
        prompt = conn.execute("SELECT name, version, updated_at FROM prompt_templates WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "user_count": user_count, "audit_count": audit_count, "prompt": prompt},
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
    """为下一批资料上传、切片和向量化功能保留后台入口。"""

    return templates.TemplateResponse(request, "knowledge.html", {"user": user})
