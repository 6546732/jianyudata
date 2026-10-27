import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_mail_watchdog as watchdog


class DailyMailWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.logs = Path(self.temporary.name)
        self.day = "2026-10-10"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        (self.logs / name).write_text(json.dumps(value), encoding="utf-8")

    def test_combined_report_contains_download_and_upload_metrics(self):
        self.write("20261010-103000-summary.json", {
            "run_started_at": self.day + "T10:30:00", "run_status": "成功",
            "export": {"rows": 792, "batches": 7, "local_status": "成功",
                       "local_verified_rows": 792, "completed_date_start": "2025-05-20",
                       "completed_date_end": "2025-05-22", "progress_date": "2025-05-22",
                       "progress_status": "waiting_quota", "remaining_quota": 8}})
        self.write("salesforce-20261010-110000-summary.json", {
            "run_started_at": self.day + "T11:00:00", "status": "成功",
            "successful_rows": 792, "failed_rows": 0, "attempted_rows": 792,
            "inserted_rows": 174, "updated_rows": 618,
            "duplicates_filtered": 19, "categories_filtered": 51, "job_id": "750"})
        with patch.object(watchdog, "LOGS", self.logs):
            subject, body, success = watchdog.combined_report(self.day)
        self.assertFalse(success)
        self.assertIn("[沙盒（生产未执行）]", subject)
        self.assertIn("2025-05-20 至 2025-05-22", body)
        self.assertIn("插入：174 条", body)
        self.assertIn("更新：618 条", body)
        self.assertIn("生产 sfoa-prod：未生成汇总（异常）", body)
        self.write("salesforce-production-20261010-111000-summary.json", {
            "run_started_at": self.day + "T11:10:00", "status": "失败",
            "successful_rows": 0, "failure_reason": "认证过期"})
        with patch.object(watchdog, "LOGS", self.logs):
            subject, body, success = watchdog.combined_report(self.day)
        self.assertFalse(success)
        self.assertIn("认证过期", body)
        self.write("salesforce-production-20261010-111000-summary.json", {
            "run_started_at": self.day + "T11:10:00", "status": "成功",
            "successful_rows": 100})
        with patch.object(watchdog, "LOGS", self.logs):
            subject, body, success = watchdog.combined_report(self.day)
        self.assertTrue(success)
        self.assertIn("生产上传100条", subject)


if __name__ == "__main__":
    unittest.main()
