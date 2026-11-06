"""受控协作任务的 JSON API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import current_user, require_action
from app.models.schemas import Action, Role, UserView
from app.services.audit import new_trace_id, write_audit_log
from app.services.task_service import (
    TaskWorkflowError,
    create_task,
    get_task,
    list_task_events,
    metrics,
    review_task,
    transition_task,
)
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/v1/tasks", tags=["受控协作任务"])


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    owner: str = Field(min_length=1, max_length=80)
    requester_role: Role = Role.STUDENT
    rule_version: str | None = Field(default=None, max_length=40)
    consent_confirmed: bool = False
    source_reference: str | None = Field(default=None, max_length=300)


class TaskTransitionRequest(BaseModel):
    target_status: str = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=1000)
    experiment_record: str | None = Field(default=None, max_length=300)
    code_commit: str | None = Field(default=None, max_length=128)
    reproduction_status: str | None = Field(default=None, max_length=20)


class TaskReviewRequest(BaseModel):
    decision: str = Field(min_length=2, max_length=10)
    comment: str = Field(min_length=1, max_length=3000)
    acceptance_criteria: str = Field(default="", max_length=3000)
    version_verified: bool
    reproduction_status: str = Field(min_length=2, max_length=20)


def _workflow_error(exc: TaskWorkflowError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                         detail={"code": "TASK_WORKFLOW_INVALID", "message": str(exc)})


@router.post("", status_code=status.HTTP_201_CREATED)
def create_controlled_task(payload: TaskCreateRequest, user: UserView = Depends(require_action(Action.EDIT))):
    result = create_task(**payload.model_dump(), actor=user)
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="controlled_task_created",
                    result_status="success", detail=f"创建任务 #{result['task']['id']}",
                    risk_flag="manual_review" if not result["controls"].ready else None)
    return {"task": result["task"], "ready_for_assignment": result["controls"].ready,
            "control_reasons": result["controls"].reasons}


@router.get("/metrics/summary")
def controlled_task_metrics(user: UserView = Depends(require_action(Action.REVIEW))):
    return metrics()


@router.get("/{task_id}")
def read_controlled_task(task_id: int, user: UserView = Depends(current_user)):
    try:
        return {"task": get_task(task_id), "events": list_task_events(task_id)}
    except TaskWorkflowError as exc:
        raise _workflow_error(exc) from None


@router.post("/{task_id}/transition")
def transition_controlled_task(task_id: int, payload: TaskTransitionRequest,
                               user: UserView = Depends(require_action(Action.EDIT))):
    try:
        task = transition_task(task_id=task_id, actor=user, **payload.model_dump())
    except TaskWorkflowError as exc:
        raise _workflow_error(exc) from None
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="controlled_task_transition",
                    result_status="success", detail=f"任务 #{task_id} 流转至 {task['status']}")
    return {"task": task, "events": list_task_events(task_id)}
@router.post("/{task_id}/review")
def review_controlled_task(task_id: int, payload: TaskReviewRequest,
                           user: UserView = Depends(require_action(Action.REVIEW))):
    try:
        task = review_task(task_id=task_id, actor=user, **payload.model_dump())
    except TaskWorkflowError as exc:
        raise _workflow_error(exc) from None
    write_audit_log(trace_id=new_trace_id(), user_id=user.id, role=user.role.value, event_type="controlled_task_review",
                    result_status="success", detail=f"任务 #{task_id} 审核后为 {task['status']}")
    return {"task": task, "events": list_task_events(task_id)}


