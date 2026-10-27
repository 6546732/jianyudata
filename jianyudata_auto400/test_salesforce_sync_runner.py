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

    def test_filter_breakdown_is_summed_for_mail(self):
        with patch.object(runner, 'BASE', self.base):
            summary = runner.build_summary(self.started, {
                'status': 'uploaded', 'rows': 10,
                'categories_filtered': {'合同': 3, '验收': 2},
                'duplicates_filtered': 1,
            })
        self.assertEqual(summary['categories_filtered'], 5)
        self.assertEqual(summary['duplicates_filtered'], 1)

    def test_partial_upload_reports_quarantined_oversized_rows(self):
        with patch.object(runner, 'BASE', self.base):
            summary = runner.build_summary(self.started, {
                'status': 'uploaded_with_blocked', 'rows': 789,
                'blocked_rows': 1,
                'blocked_rows_file': 'D:\\data\\sfoa_upload\\blocked_rows_pending.csv',
            })
        self.assertEqual(summary['status'], '部分成功')
        self.assertEqual(summary['uploaded_rows'], 789)
        self.assertEqual(summary['blocked_rows'], 1)
        self.assertIn('完整原文队列', runner.summary_text(summary))

    def test_failure_reports_zero_uploaded_and_reason(self):
        with patch.object(runner, 'BASE', self.base):
            summary = runner.build_summary(
                self.started, error_text='RuntimeError: Salesforce CLI 未返回 JSON')
        self.assertEqual(summary['status'], '失败')
        self.assertEqual(summary['uploaded_rows'], 0)
        self.assertIn('Salesforce CLI 未返回 JSON', summary['failure_reason'])


if __name__ == '__main__':
    unittest.main()
