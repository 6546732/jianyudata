import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import run_scheduled_notebook as runner


def write_xlsx(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('xl/workbook.xml', '<workbook/>')


class RunSummaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.started = datetime(2026, 9, 24, 10, 30)
        self.file = self.base / 'downloads' / 'batch' / 'data.xlsx'
        write_xlsx(self.file)
        (self.base / 'range_progress.json').write_text(json.dumps({
            'current_date': '2025-03-27', 'status': 'waiting_quota',
            'remaining': 2, 'pending': None,
            'completed': [{
                'quota_day': '2026-09-24', 'count': 798,
                'file': str(self.file),
            }],
        }), encoding='utf-8')
        (self.base / 'sfoa_upload').mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_success_summary_reports_download_and_upload_rows(self):
        log = self.base / 'run.log'
        log.write_text("Salesforce 同步： {'status': 'uploaded', 'rows': 790, 'files': 8}\n",
                       encoding='utf-8')
        with patch.object(runner, 'BASE', self.base):
            summary = runner.collect_summary(log, self.started, expect_salesforce=True)
        self.assertEqual(summary['export']['rows'], 798)
        self.assertEqual(summary['export']['local_verified_rows'], 798)
        self.assertEqual(summary['salesforce']['uploaded_rows'], 790)
        self.assertEqual(summary['salesforce']['status'], '成功')

    def test_failure_summary_is_concise_and_has_no_log_attachment_text(self):
        report = self.base / 'sfoa_upload' / 'prepare_report.json'
        report.write_text(json.dumps({'rows_written': 3130}), encoding='utf-8')
        timestamp = self.started.timestamp() + 1
        import os
        os.utime(report, (timestamp, timestamp))
        log = self.base / 'run.log'
        log.write_text('traceback details\n', encoding='utf-8')
        with patch.object(runner, 'BASE', self.base):
            summary = runner.collect_summary(
                log, self.started, failed=True,
                error_text='Traceback...\nRuntimeError: Salesforce CLI 未返回 JSON',
                expect_salesforce=True)
        message = runner.summary_text(summary)
        self.assertEqual(summary['salesforce']['uploaded_rows'], 0)
        self.assertEqual(summary['salesforce']['attempted_rows'], 3130)
        self.assertIn('失败原因：\nRuntimeError: Salesforce CLI 未返回 JSON', message)
        self.assertNotIn('完整日志', message)
        self.assertNotIn('附件', message)

    def test_export_only_summary_waits_for_independent_upload(self):
        log = self.base / 'run.log'
        log.write_text('本地导出与文件校验完成\n', encoding='utf-8')
        with patch.object(runner, 'BASE', self.base):
            summary = runner.collect_summary(log, self.started)
        self.assertEqual(summary['salesforce']['status'], '等待独立上传任务')
        self.assertNotIn('未取得 Salesforce', summary['failure_reason'])


if __name__ == '__main__':
    unittest.main()
