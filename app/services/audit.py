"""应用审计服务；不记录密码、JWT 或完整资料正文。"""

from __future__ import annotations

import uuid

from app.services.database import connection


def new_trace_id() -> str:
    """每次请求生成可关联工具日志与问答日志的链路标识。"""

    return uuid.uuid4().hex


def write_audit_log(
    *, trace_id: str, user_id: int | None, role: str | None, event_type: str,
    result_status: str, detail: str, risk_flag: str | None = None,
) -> None:
    """写入结构化审计事件。"""

    with connection() as conn:
        conn.execute(
            """INSERT INTO audit_logs(trace_id, user_id, role, event_type, result_status, detail, risk_flag)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (trace_id, user_id, role, event_type, result_status, detail[:500], risk_flag),
        )
