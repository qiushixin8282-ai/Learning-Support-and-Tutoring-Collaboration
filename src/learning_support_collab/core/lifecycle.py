from learning_support_collab.core.models import SupportTask, TaskStatus


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    "received": {"assigned"},
    "assigned": {"drafted"},
    "drafted": {"under_review"},
    "under_review": {"returned_for_revision", "published"},
    "returned_for_revision": {"drafted"},
    "published": {"handed_off"},
    "handed_off": set(),
}


def transition(task: SupportTask, target: TaskStatus, *, actor: str, reason: str = "") -> SupportTask:
    """Move a task only through the workflow shown in the process diagrams."""
    if target not in ALLOWED_TRANSITIONS[task.status]:
        raise ValueError(f"不允许从 {task.status} 直接流转到 {target}")
    if target == "published":
        task.version += 1
    task.audit_events.append(
        f"actor:{actor}; transition:{task.status}->{target}; reason:{reason or 'not_provided'}"
    )
    task.status = target
    return task
