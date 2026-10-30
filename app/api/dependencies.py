"""FastAPI 鉴权依赖；所有受保护接口复用此处的 JWT 与权限检查。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.schemas import Action, Role, UserView
from app.services.database import connection
from app.services.security import allows, decode_access_token


bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "AUTH_REQUIRED", "message": message})


def _read_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """兼容 Bearer Header 与服务端渲染页面使用的 HttpOnly Cookie。"""

    return credentials.credentials if credentials else request.cookies.get("access_token")


def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserView:
    """从 JWT 与数据库加载当前有效用户。"""

    token = _read_token(request, credentials)
    if not token:
        raise _unauthorized("请先登录后再访问")
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized("登录已失效，请重新登录") from None
    with connection() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or not row["is_active"]:
        raise _unauthorized("账号不存在或已被停用")
    return UserView(id=row["id"], email=row["email"], display_name=row["display_name"], role=Role(row["role"]))


def require_action(action: Action) -> Callable[[UserView], UserView]:
    """构造接口依赖，统一返回 403 中文错误而非依赖模型自行判断。"""

    def dependency(user: UserView = Depends(current_user)) -> UserView:
        if not allows(user.role, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PERMISSION_DENIED", "message": f"当前角色没有“{action.value}”权限"},
            )
        return user

    return dependency


def require_admin(user: UserView = Depends(current_user)) -> UserView:
    """后台治理页面只允许系统管理员访问。"""

    if user.role != Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ADMIN_REQUIRED", "message": "仅系统管理员可以访问管理后台"},
        )
    return user
