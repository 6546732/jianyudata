import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import run_salesforce_sync as runner


class SalesforceSyncRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        (self.base / 'sfoa_upload').mkdir()
        self.started = datetime(2026, 9, 24, 12, 0)

    def tearDown(self):
        self.temporary.cleanup()

    def test_success_reports_actual_uploaded_rows(self):
        with patch.object(runner, 'BASE', self.base):
            summary = runner.build_summary(
                self.started, {'status': 'uploaded', 'rows': 790, 'job_id': '750xx'})
        self.assertEqual(summary['status'], '成功')
        self.assertEqual(summary['uploaded_rows'], 790)
        self.assertEqual(summary['job_id'], '750xx')

    def test_failure_reports_zero_uploaded_and_reason(self):
        with patch.object(runner, 'BASE', self.base):
            summary = runner.build_summary(
                self.started, error_text='RuntimeError: Salesforce CLI 未返回 JSON')
        self.assertEqual(summary['status'], '失败')
        self.assertEqual(summary['uploaded_rows'], 0)
        self.assertIn('Salesforce CLI 未返回 JSON', summary['failure_reason'])


if __name__ == '__main__':
    unittest.main()
