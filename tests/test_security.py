"""登录与权限服务的基础回归测试。"""

import unittest

from app.models.schemas import Action, Role
from app.services.security import allows, hash_password, verify_password


class SecurityTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        encoded = hash_password("local-demo-password")
        self.assertTrue(verify_password("local-demo-password", encoded))
        self.assertFalse(verify_password("incorrect-password", encoded))

    def test_role_permission_matrix(self):
        self.assertTrue(allows(Role.STUDENT, Action.VIEW))
        self.assertFalse(allows(Role.STUDENT, Action.EDIT))
        self.assertTrue(allows(Role.MENTOR, Action.REVIEW))
        self.assertFalse(allows(Role.MENTOR, Action.PUBLISH))
        self.assertTrue(allows(Role.GROUP_LEADER, Action.PUBLISH))
        self.assertTrue(allows(Role.SYSTEM_ADMIN, Action.PUBLISH))


if __name__ == "__main__":
    unittest.main()
