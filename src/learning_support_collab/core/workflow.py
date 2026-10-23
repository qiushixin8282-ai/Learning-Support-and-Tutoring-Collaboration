from learning_support_collab.core.models import Evaluation, SupportRequest


def evaluate_request(request: SupportRequest, *, required_rule_version: str) -> Evaluation:
    """Apply non-negotiable controls before a request enters an assignment queue."""
    reasons: list[str] = []
    audit = [f"request:{request.request_id}:received", "control:authorization_checked"]

    if not request.consent_confirmed:
        reasons.append("缺少已确认的学习支持授权")
    if request.rule_version != required_rule_version:
        reasons.append(f"规则版本不匹配；需要 {required_rule_version}")
    if not request.assigned_owner:
        reasons.append("尚未指定具名处理责任人")

    if reasons:
        audit.append("decision:manual_review")
        return Evaluation(
            request_id=request.request_id,
            decision="needs_human_review",
            reasons=reasons,
            next_owner="学习服务主任",
            audit_events=audit,
        )

    audit.append("decision:ready_for_assignment")
    return Evaluation(
        request_id=request.request_id,
        decision="ready_for_assignment",
        reasons=["授权、规则版本和责任人均已核对"],
        next_owner=request.assigned_owner,
        audit_events=audit,
    )
