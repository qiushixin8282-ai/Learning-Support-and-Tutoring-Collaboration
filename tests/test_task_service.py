"""受控任务闭环及指标的回归测试。"""

import os
import tempfile
import unittest
from pathlib import Path

from app.models.schemas import Role, UserView
from app.services.database import connection, initialize_database
from app.services.task_service import (
    TaskWorkflowError,
    create_task,
    get_task,
    list_task_events,
    metrics,
    review_task,
    transition_task,
)


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self.temp.name) / "tasks.sqlite3")
        initialize_database()
        self.ta = UserView(id=1, email="ta@example.com", display_name="助教甲", role=Role.TEACHING_ASSISTANT)
        self.mentor = UserView(id=2, email="mentor@example.com", display_name="导师乙", role=Role.MENTOR)
        with connection() as conn:
            conn.executemany(
                "INSERT INTO users(id, email, display_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                [
                    (self.ta.id, self.ta.email, self.ta.display_name, "unused", self.ta.role.value),
                    (self.mentor.id, self.mentor.email, self.mentor.display_name, "unused", self.mentor.role.value),
                ],
            )

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.previous
        self.temp.cleanup()

    def _create_ready_task(self):
        return create_task(
            title="复现实验成果", description="补齐记录并审核", owner="导师乙", requester_role=Role.STUDENT,
            rule_version="2026.10", consent_confirmed=True, source_reference="资料-v2.3", actor=self.ta,
        )["task"]

    def test_missing_controls_block_assignment(self):
        result = create_task(
            title="资料不完整", description="", owner="导师乙", requester_role=Role.STUDENT,
            rule_version="2026.09", consent_confirmed=False, source_reference=None, actor=self.ta,
        )
        self.assertFalse(result["controls"].ready)
        with self.assertRaisesRegex(TaskWorkflowError, "无法自动分派"):
            transition_task(task_id=result["task"]["id"], target_status="已分派", actor=self.ta)

    def test_review_requires_evidence_and_records_revision(self):
        task = self._create_ready_task()
        task = transition_task(task_id=task["id"], target_status="已分派", actor=self.ta)
        task = transition_task(task_id=task["id"], target_status="进行中", actor=self.ta)
        with self.assertRaisesRegex(TaskWorkflowError, "实验记录"):
            transition_task(task_id=task["id"], target_status="待审核", actor=self.ta)
        task = transition_task(task_id=task["id"], target_status="待审核", actor=self.ta,
                               experiment_record="run-20261014", code_commit="8f3a2c1")
        task = review_task(task_id=task["id"], decision="退回修订", comment="补充分组分析",
                           acceptance_criteria="包含低参与度学生分组", actor=self.mentor,
                           version_verified=False, reproduction_status="部分可复现")
        self.assertEqual(task["status"], "需修订")
        self.assertEqual(task["revision_count"], 1)
        self.assertGreaterEqual(len(list_task_events(task["id"])), 5)

    def test_approved_review_updates_metrics(self):
        task = self._create_ready_task()
        for target in ("已分派", "进行中"):
            task = transition_task(task_id=task["id"], target_status=target, actor=self.ta)
        task = transition_task(task_id=task["id"], target_status="待审核", actor=self.ta,
                               experiment_record="run-20261016", code_commit="abcdef1")
        task = review_task(task_id=task["id"], decision="通过", comment="资料齐全，可用于组会",
                           acceptance_criteria="版本、实验记录和分组分析完整", actor=self.mentor,
                           version_verified=True, reproduction_status="可复现")
        self.assertEqual(get_task(task["id"])["status"], "已完成")
        summary = metrics()
        self.assertEqual(summary["task_count"], 1)
        self.assertEqual(summary["version_correct_rate"], 1.0)
        self.assertEqual(summary["reproducible_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
