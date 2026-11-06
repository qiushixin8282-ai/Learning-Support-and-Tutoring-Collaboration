"""运行环境校验与面向公网入口的轻量限流。"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def is_production() -> bool:
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def secure_cookie() -> bool:
    return is_production()


def trusted_hosts() -> list[str]:
    """读取允许的 Host；开发环境保留本机访问体验。"""

    configured = [host.strip() for host in os.getenv("TRUSTED_HOSTS", "").split(",") if host.strip()]
    return configured or ["localhost", "127.0.0.1", "testserver"]


def validate_runtime() -> None:
    """在生产启动前拒绝开发密钥和缺失的主机白名单。"""

    if not is_production():
        return
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32 or secret == "development-only-secret-change-before-production":
        raise RuntimeError("生产环境必须设置至少32位的 JWT_SECRET")
    if not os.getenv("TRUSTED_HOSTS", "").strip():
        raise RuntimeError("生产环境必须设置 TRUSTED_HOSTS")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端与路径限流，保护登录和问答入口免受瞬时滥用。"""

    protected_paths = {"/api/auth/login", "/admin/rag/ask"}

    def __init__(self, app, requests_per_minute: int = 30):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.windows: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in self.protected_paths:
            key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}"
            now = time.monotonic(); window = self.windows[key]
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= self.requests_per_minute:
                return JSONResponse(status_code=429, content={"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"})
            window.append(now)
        return await call_next(request)
