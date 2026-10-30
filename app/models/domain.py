from dataclasses import asdict, dataclass, field
from typing import Literal


Decision = Literal["ready_for_assignment", "needs_human_review"]
TaskStatus = Literal[
    "received",
    "assigned",
    "drafted",
    "under_review",
    "returned_for_revision",
    "published",
    "handed_off",
]


@dataclass(frozen=True)
class SupportRequest:
    request_id: str
    learner_region: str
    topic: str
    rule_version: str | None
    consent_confirmed: bool
    requester_role: str
    assigned_owner: str | None = None


@dataclass
class Evaluation:
    request_id: str
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    next_owner: str | None = None
    audit_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SupportTask:
    """A minimal auditable lifecycle for a learning-support response."""

    task_id: str
    owner: str
    status: TaskStatus = "received"
    version: int = 0
    audit_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
