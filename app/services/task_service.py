"""受控协作任务的持久化工作流与指标服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models.schemas import Role, UserView
from app.services.database import connection


REQUIRED_RULE_VERSION = "2026.10"
REPRODUCTION_STATUSES = {"未测试", "可复现", "部分可复现", "不可复现"}
TRANSITIONS = {
    "新建": {"已分派"},
    "已分派": {"进行中"},
    "进行中": {"待审核"},
    "待审核": {"需修订", "已完成"},
    "需修订": {"进行中"},
    "已完成": {"已归档"},
    "已归档": set(),
}


class TaskWorkflowError(ValueError):
    """用户可修正的任务数据或工作流错误。"""


@dataclass(frozen=True)
class ControlResult:
    ready: bool
    reasons: list[str]


def evaluate_controls(*, rule_version: str | None, consent_confirmed: bool, owner: str) -> ControlResult:
    """在自动分派前执行授权、规则版本和具名责任人三项硬校验。"""

    reasons: list[str] = []
    if not consent_confirmed:
        reasons.append("缺少已确认的学习支持授权")
    if rule_version != REQUIRED_RULE_VERSION:
        reasons.append(f"规则版本不匹配；当前需要 {REQUIRED_RULE_VERSION}")
    if not owner.strip():
        reasons.append("尚未指定具名处理责任人")
    return ControlResult(ready=not reasons, reasons=reasons)


def _row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["consent_confirmed"] = bool(item["consent_confirmed"])
    item["version_verified"] = bool(item["version_verified"])
    return item


def _record_event(*, task_id: int, actor: UserView, event_type: str, from_status: str | None,
                  to_status: str | None, reason: str = "", detail: dict[str, Any] | None = None) -> None:
    with connection() as conn:
        conn.execute(
            """INSERT INTO task_events(task_id, actor_user_id, actor_role, event_type, from_status, to_status, reason, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, actor.id, actor.role.value, event_type, from_status, to_status, reason,
             json.dumps(detail or {}, ensure_ascii=False)),
        )


def create_task(*, title: str, description: str, owner: str, requester_role: Role, rule_version: str | None,
                consent_confirmed: bool, source_reference: str | None, actor: UserView) -> dict[str, Any]:
    controls = evaluate_controls(rule_version=rule_version, consent_confirmed=consent_confirmed, owner=owner)
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO support_tasks(
                title, description, owner, status, requester_role, rule_version, consent_confirmed,
                source_reference, created_by_user_id
            ) VALUES (?, ?, ?, '新建', ?, ?, ?, ?, ?)""",
            (title.strip(), description.strip(), owner.strip(), requester_role.value, rule_version,
             int(consent_confirmed), source_reference.strip() if source_reference else None, actor.id),
        )
        task_id = int(cursor.lastrowid)
    _record_event(task_id=task_id, actor=actor, event_type="created", from_status=None, to_status="新建",
                  detail={"control_ready": controls.ready, "control_reasons": controls.reasons})
    return {"task": get_task(task_id), "controls": controls}


def get_task(task_id: int) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM support_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise TaskWorkflowError("任务不存在")
    return _row_to_dict(row)


def list_task_events(task_id: int) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
    return [dict(row) for row in rows]


def transition_task(*, task_id: int, target_status: str, actor: UserView, reason: str = "",
                    experiment_record: str | None = None, code_commit: str | None = None,
                    reproduction_status: str | None = None) -> dict[str, Any]:
    task = get_task(task_id)
    current = task["status"]
    if target_status not in TRANSITIONS.get(current, set()):
        raise TaskWorkflowError(f"不允许从“{current}”流转到“{target_status}”")
    if target_status == "已分派":
        controls = evaluate_controls(rule_version=task["rule_version"], consent_confirmed=task["consent_confirmed"], owner=task["owner"])
        if not controls.ready:
            raise TaskWorkflowError("无法自动分派：" + "；".join(controls.reasons))
    if target_status == "待审核":
        missing = [name for name, value in {"来源资料": task["source_reference"], "实验记录": experiment_record or task["experiment_record"], "代码 commit": code_commit or task["code_commit"]}.items() if not value]
        if missing:
            raise TaskWorkflowError("提交审核前必须补齐：" + "、".join(missing))
    if reproduction_status and reproduction_status not in REPRODUCTION_STATUSES:
        raise TaskWorkflowError("不支持的实验复现状态")
    next_reproduction = reproduction_status or task["reproduction_status"]
    revision_increment = 1 if target_status == "需修订" else 0
    with connection() as conn:
        conn.execute(
            """UPDATE support_tasks
               SET status = ?, experiment_record = COALESCE(?, experiment_record), code_commit = COALESCE(?, code_commit),
                   reproduction_status = ?, revision_count = revision_count + ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (target_status, experiment_record, code_commit, next_reproduction, revision_increment, task_id),
        )
    _record_event(task_id=task_id, actor=actor, event_type="status_changed", from_status=current,
                  to_status=target_status, reason=reason, detail={"reproduction_status": next_reproduction})
    return get_task(task_id)


def review_task(*, task_id: int, decision: str, comment: str, acceptance_criteria: str, actor: UserView,
                version_verified: bool, reproduction_status: str) -> dict[str, Any]:
    if decision not in {"通过", "退回修订", "拒绝"}:
        raise TaskWorkflowError("审核决定仅支持：通过、退回修订、拒绝")
    if not comment.strip():
        raise TaskWorkflowError("审核意见不能为空")
    if reproduction_status not in REPRODUCTION_STATUSES:
        raise TaskWorkflowError("不支持的实验复现状态")
    task = get_task(task_id)
    if task["status"] != "待审核":
        raise TaskWorkflowError("只有待审核任务可以填写审核决定")
    target = "已完成" if decision == "通过" else "需修订"
    if decision == "拒绝":
        target = "已归档"
    with connection() as conn:
        conn.execute(
            """INSERT INTO task_reviews(task_id, reviewer_user_id, decision, comment, acceptance_criteria)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, actor.id, decision, comment.strip(), acceptance_criteria.strip()),
        )
        conn.execute(
            """UPDATE support_tasks SET status = ?, version_verified = ?, reproduction_status = ?,
               revision_count = revision_count + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (target, int(version_verified), reproduction_status, int(target == "需修订"), task_id),
        )
    _record_event(task_id=task_id, actor=actor, event_type="reviewed", from_status="待审核", to_status=target,
                  reason=comment, detail={"decision": decision, "version_verified": version_verified,
                                           "reproduction_status": reproduction_status})
    return get_task(task_id)


def metrics() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total, COALESCE(SUM(revision_count), 0) AS revisions,
                      COALESCE(AVG(revision_count), 0) AS average_revisions,
                      COALESCE(AVG(version_verified), 0) AS version_correct_rate,
                      SUM(CASE WHEN reproduction_status = '可复现' THEN 1 ELSE 0 END) AS reproducible,
                      SUM(CASE WHEN reproduction_status != '未测试' THEN 1 ELSE 0 END) AS reproduction_tested
               FROM support_tasks"""
        ).fetchone()
    total = int(row["total"])
    tested = int(row["reproduction_tested"])
    return {
        "task_count": total,
        "revision_count": int(row["revisions"]),
        "average_revisions": round(float(row["average_revisions"]), 2),
        "version_correct_rate": round(float(row["version_correct_rate"]), 4),
        "reproduction_tested_count": tested,
        "reproducible_count": int(row["reproducible"]),
        "reproducible_rate": round(int(row["reproducible"]) / tested, 4) if tested else None,
    }
