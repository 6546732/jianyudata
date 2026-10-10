import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import salesforce_auth as auth


class SalesforceJwtAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.key = Path(self.temporary.name) / "server.key"
        self.key.write_text("test-only", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def completed(self, result, returncode=0):
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps({"status": returncode, "result": result}),
            stderr="",
        )

    @patch("salesforce_auth.subprocess.run")
    def test_login_accepts_only_expected_sandbox(self, run):
        run.return_value = self.completed({
            "orgId": auth.JWT_EXPECTED_ORG_ID,
            "username": auth.JWT_USERNAME,
            "instanceUrl": auth.JWT_INSTANCE_URL,
            "isSandbox": True,
        })
        with patch.object(auth, "JWT_KEY_FILE", self.key):
            result = auth.login_zihao_jwt()
        self.assertEqual(result["status"], "authenticated")
        self.assertTrue(result["is_sandbox"])
        command = run.call_args.args[0]
        self.assertIn("jwt", command)
        self.assertNotIn("password", " ".join(command).lower())

    @patch("salesforce_auth.subprocess.run")
    def test_login_rejects_wrong_org(self, run):
        run.return_value = self.completed({
            "orgId": "00DPRODUCTION",
            "username": auth.JWT_USERNAME,
            "instanceUrl": "https://example.invalid",
            "isSandbox": False,
        })
        with patch.object(auth, "JWT_KEY_FILE", self.key):
            with self.assertRaisesRegex(RuntimeError, "错误组织"):
                auth.login_zihao_jwt()

    def test_login_requires_private_key(self):
        with patch.object(auth, "JWT_KEY_FILE", self.key.with_name("missing.key")):
            with self.assertRaisesRegex(RuntimeError, "私钥不存在"):
                auth.login_zihao_jwt()


if __name__ == "__main__":
    unittest.main()

