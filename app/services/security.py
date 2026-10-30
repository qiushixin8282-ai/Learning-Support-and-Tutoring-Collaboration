"""密码哈希、JWT 与接口级角色权限校验。"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.models.schemas import Action, Role


ROLE_PERMISSIONS: dict[Role, set[Action]] = {
    Role.STUDENT: {Action.VIEW},
    Role.TEACHING_ASSISTANT: {Action.VIEW, Action.EDIT},
    Role.MENTOR: {Action.VIEW, Action.EDIT, Action.REVIEW},
    Role.GROUP_LEADER: {Action.VIEW, Action.EDIT, Action.REVIEW, Action.PUBLISH},
    Role.SYSTEM_ADMIN: {Action.VIEW, Action.EDIT, Action.REVIEW, Action.PUBLISH},
}


def jwt_secret() -> str:
    """生产环境必须显式配置 JWT_SECRET，避免使用公开默认值。"""

    secret = os.getenv("JWT_SECRET")
    if secret:
        return secret
    if os.getenv("APP_ENV", "development") == "production":
        raise RuntimeError("生产环境必须设置 JWT_SECRET")
    return "development-only-secret-change-before-production"


def hash_password(password: str, salt: str | None = None) -> str:
    """使用 PBKDF2 生成可存储的盐值密码哈希。"""

    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 310_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """常量时间比较，避免密码校验泄露时序信息。"""

    try:
        algorithm, salt, digest = encoded.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(hash_password(password, salt), encoded)


def create_access_token(user_id: int, role: Role) -> str:
    """签发短时 JWT；角色仍须在接口处与业务权限共同校验。"""

    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(hours=8),
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    """验证签名及有效期；失败时由 API 返回统一中文错误。"""

    return jwt.decode(token, jwt_secret(), algorithms=["HS256"])


def allows(role: Role, action: Action) -> bool:
    """判断角色是否拥有某类动作权限。"""

    return action in ROLE_PERMISSIONS[role]
