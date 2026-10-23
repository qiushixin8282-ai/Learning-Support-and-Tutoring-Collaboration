import unittest

from learning_support_collab.core.models import SupportRequest
from learning_support_collab.core.models import SupportTask
from learning_support_collab.core.lifecycle import transition
from learning_support_collab.main import run_lifecycle_demo
from learning_support_collab.core.workflow import evaluate_request


class WorkflowTests(unittest.TestCase):
    def test_ready_only_when_all_controls_pass(self):
        request = SupportRequest("R-1", "CN", "反馈", "2026.10", True, "助教", "课程讲师")
        result = evaluate_request(request, required_rule_version="2026.10")
        self.assertEqual(result.decision, "ready_for_assignment")
        self.assertEqual(result.next_owner, "课程讲师")

    def test_missing_controls_require_human_review(self):
        request = SupportRequest("R-2", "CN", "反馈", None, False, "助教")
        result = evaluate_request(request, required_rule_version="2026.10")
        self.assertEqual(result.decision, "needs_human_review")
        self.assertEqual(result.next_owner, "学习服务主任")
        self.assertGreaterEqual(len(result.reasons), 3)

    def test_task_can_be_reviewed_published_and_handed_off(self):
        task = SupportTask("T-1", "课程讲师")
        transition(task, "assigned", actor="学习服务主任")
        transition(task, "drafted", actor="课程讲师")
        transition(task, "under_review", actor="课程运营员")
        transition(task, "published", actor="课程运营员", reason="复核通过")
        transition(task, "handed_off", actor="平台工程师")
        self.assertEqual(task.status, "handed_off")
        self.assertEqual(task.version, 1)

    def test_task_cannot_skip_review(self):
        task = SupportTask("T-2", "课程讲师")
        with self.assertRaises(ValueError):
            transition(task, "published", actor="课程讲师")

    def test_lifecycle_demo_produces_auditable_handoff(self):
        result = run_lifecycle_demo()
        self.assertEqual(result["status"], "handed_off")
        self.assertEqual(len(result["audit_events"]), 5)


if __name__ == "__main__":
    unittest.main()
