"""任务 API 的认证、硬控制与审核流程测试。"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app
from app.models.schemas import Role
from app.services.database import connection, initialize_database
from app.services.security import create_access_token, hash_password


# Starlette TestClient 在部分 Windows/Proactor 环境无法创建本地 socketpair；
# CI 的 Linux 环境不受影响，本地测试显式使用 Selector 事件循环。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@unittest.skipIf(sys.platform == "win32", "当前受限 Windows 环境无法创建 TestClient 所需的本地 socketpair；CI 在 Linux 执行")
class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self.temp.name) / "api.sqlite3")
        initialize_database()
        with connection() as conn:
            conn.executemany(
                "INSERT INTO users(id, email, display_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "student@example.com", "学生甲", hash_password("StrongPass2026"), Role.STUDENT.value),
                    (2, "ta@example.com", "助教乙", hash_password("StrongPass2026"), Role.TEACHING_ASSISTANT.value),
                    (3, "mentor@example.com", "导师丙", hash_password("StrongPass2026"), Role.MENTOR.value),
                ],
            )
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        if self.previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.previous
        self.temp.cleanup()

    @staticmethod
    def auth(user_id: int, role: Role) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user_id, role)}"}

    def test_role_control_and_reviewable_lifecycle(self):
        payload = {
            "title": "复现实验结果", "description": "需要导师终审", "owner": "导师丙",
            "requester_role": "学生", "rule_version": "2026.10", "consent_confirmed": True,
            "source_reference": "数据说明-v2.3",
        }
        denied = self.client.post("/api/v1/tasks", json=payload, headers=self.auth(1, Role.STUDENT))
        self.assertEqual(denied.status_code, 403)
        created = self.client.post("/api/v1/tasks", json=payload, headers=self.auth(2, Role.TEACHING_ASSISTANT))
        self.assertEqual(created.status_code, 201)
        task_id = created.json()["task"]["id"]
        for target in ("已分派", "进行中"):
            response = self.client.post(f"/api/v1/tasks/{task_id}/transition", json={"target_status": target},
                                        headers=self.auth(2, Role.TEACHING_ASSISTANT))
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post(f"/api/v1/tasks/{task_id}/transition", json={"target_status": "待审核"},
                                   headers=self.auth(2, Role.TEACHING_ASSISTANT))
        self.assertEqual(blocked.status_code, 422)
        submitted = self.client.post(
            f"/api/v1/tasks/{task_id}/transition",
            json={"target_status": "待审核", "experiment_record": "run-20261016", "code_commit": "abcdef1"},
            headers=self.auth(2, Role.TEACHING_ASSISTANT),
        )
        self.assertEqual(submitted.status_code, 200)
        reviewed = self.client.post(
            f"/api/v1/tasks/{task_id}/review",
            json={"decision": "通过", "comment": "证据完整", "acceptance_criteria": "记录与版本一致",
                  "version_verified": True, "reproduction_status": "可复现"},
            headers=self.auth(3, Role.MENTOR),
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["task"]["status"], "已完成")
        summary = self.client.get("/api/v1/tasks/metrics/summary", headers=self.auth(3, Role.MENTOR))
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["version_correct_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
