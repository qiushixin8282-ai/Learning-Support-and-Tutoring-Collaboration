import unittest

from learning_support_collab.core.models import SupportRequest
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


if __name__ == "__main__":
    unittest.main()
