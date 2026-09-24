import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sync_to_salesforce as syncer


def job(job_id="750TEST", rows=12, state="JobComplete", failed=0,
        created="2026-09-20T08:18:01.000+0000"):
    return {
        "id": job_id,
        "object": "bidnews__c",
        "operation": "upsert",
        "externalIdFieldName": "Source_Key__c",
        "state": state,
        "numberRecordsProcessed": rows,
        "numberRecordsFailed": failed,
        "createdDate": created,
    }


class BulkJobRecoveryTests(unittest.TestCase):
    def test_sandbox_keeps_legacy_state_paths(self):
        base = Path(tempfile.gettempdir()) / "jianyu-test"
        output, manifest, lock = syncer._state_paths(base, "zihao")
        self.assertEqual(output, base / "sfoa_upload")
        self.assertEqual(manifest, base / "sfoa_sync_manifest.json")
        self.assertEqual(lock, base / "sfoa_sync.lock")

    def test_production_uses_separate_state_paths(self):
        base = Path(tempfile.gettempdir()) / "jianyu-test"
        output, manifest, lock = syncer._state_paths(base, "prod/main")
        self.assertEqual(output, base / "sfoa_upload_prod_main")
        self.assertEqual(manifest, base / "sfoa_sync_manifest_prod_main.json")
        self.assertEqual(lock, base / "sfoa_sync_prod_main.lock")

    def test_cli_environment_disables_shared_log_and_telemetry(self):
        environment = syncer._sf_environment()
        self.assertEqual(environment["SF_DISABLE_LOG_FILE"], "true")
        self.assertEqual(environment["SF_DISABLE_TELEMETRY"], "true")

    def test_validate_accepts_only_exact_completed_job(self):
        value = job(rows=12)
        self.assertIs(syncer.validate_bulk_job(value, 12), value)

    def test_validate_rejects_wrong_count(self):
        with self.assertRaisesRegex(RuntimeError, "processed=11/12"):
            syncer.validate_bulk_job(job(rows=11), 12)

    def test_validate_rejects_failed_rows(self):
        with self.assertRaisesRegex(RuntimeError, "failed=1"):
            syncer.validate_bulk_job(job(rows=12, failed=1), 12)

    @patch.object(syncer, "get_bulk_job")
    @patch.object(syncer, "sf_rest")
    def test_find_recent_job_ignores_old_job(self, rest, detail):
        old = job(job_id="750OLD", rows=12,
                  created="2026-09-20T07:59:59.000+0000")
        current = job(job_id="750CURRENT", rows=12)
        rest.return_value = {"records": [old, current]}
        detail.return_value = current
        found = syncer.find_recent_bulk_job(
            "zihao", datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
            12, attempts=1)
        self.assertEqual(found["id"], "750CURRENT")
        detail.assert_called_once_with("zihao", "750CURRENT")

    @patch.object(syncer.subprocess, "run")
    def test_sf_rest_unwraps_cli_response(self, run):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        run.return_value.stdout = json.dumps({
            "status": 0,
            "result": {"statusCode": 200, "body": {"records": [job()]}}
        })
        body = syncer.sf_rest("zihao", "/services/data/v67.0/jobs/ingest")
        self.assertEqual(body["records"][0]["id"], "750TEST")

    @patch.object(syncer.time, "sleep")
    @patch.object(syncer.subprocess, "run")
    def test_sf_rest_retries_transient_non_json_output(self, run, sleep):
        failed = unittest.mock.Mock(returncode=0, stderr="", stdout="not-json")
        passed = unittest.mock.Mock(returncode=0, stderr="", stdout=json.dumps({
            "status": 0, "result": {"statusCode": 200, "body": {"ok": True}}
        }))
        run.side_effect = [failed, passed]
        self.assertTrue(syncer.sf_rest("zihao", "/limits")["ok"])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
