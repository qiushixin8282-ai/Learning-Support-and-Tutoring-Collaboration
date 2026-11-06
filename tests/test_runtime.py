"""生产环境配置与入口保护回归测试。"""

import os
import unittest

from app.services.runtime import is_production, secure_cookie, trusted_hosts, validate_runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.saved = {key: os.environ.get(key) for key in ("APP_ENV", "JWT_SECRET", "TRUSTED_HOSTS")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value

    def test_development_allows_local_defaults(self):
        os.environ["APP_ENV"] = "development"; os.environ.pop("JWT_SECRET", None)
        validate_runtime(); self.assertFalse(is_production()); self.assertFalse(secure_cookie())

    def test_production_rejects_missing_security_configuration(self):
        os.environ["APP_ENV"] = "production"; os.environ.pop("JWT_SECRET", None); os.environ.pop("TRUSTED_HOSTS", None)
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET"):
            validate_runtime()

    def test_production_accepts_strong_secret_and_hosts(self):
        os.environ.update({"APP_ENV": "production", "JWT_SECRET": "a" * 32, "TRUSTED_HOSTS": "example.com"})
        validate_runtime(); self.assertTrue(is_production()); self.assertTrue(secure_cookie())

    def test_trusted_hosts_reads_comma_separated_allowlist(self):
        os.environ["TRUSTED_HOSTS"] = "lab.example.edu, 127.0.0.1"
        self.assertEqual(trusted_hosts(), ["lab.example.edu", "127.0.0.1"])
