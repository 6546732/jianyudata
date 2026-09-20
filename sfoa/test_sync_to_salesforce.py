import json
import unittest
from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
