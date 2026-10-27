import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sync_to_salesforce as syncer


class LegacyOverlapTests(unittest.TestCase):
    def test_reviewed_history_is_not_uploaded_and_changed_files_are_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "downloads").mkdir()
            file = base / "downloads" / "history.xlsx"
            file.write_bytes(b"reviewed")
            _, manifest, _ = syncer._state_paths(base, "sfoa-prod")
            manifest.write_text(json.dumps({"files": {}, "reviewed_files": {
                str(file.resolve()): syncer.digest(file)}}), encoding="utf-8")
            self.assertEqual(syncer._pending_files(base, False, "sfoa-prod")[3], [])
            self.assertEqual(syncer._pending_files(base, True, "sfoa-prod")[3], [file])
            file.write_bytes(b"changed")
            self.assertEqual(syncer._pending_files(base, False, "sfoa-prod")[3], [file])

    def test_legacy_fallback_detects_overlap_without_external_key(self):
        local = [{"Full_Title__c": "采购公告", "Buyer_Name__c": "单位甲", "release_date__c": "2025-06-01"}]
        remote = [{"Name": "采购公告", "accountname__c": "单位甲", "release_date__c": "2025-06-01"}]
        self.assertEqual(syncer.legacy_overlap_count(local, remote), 1)
        remote[0]["release_date__c"] = "2025-06-02"
        self.assertEqual(syncer.legacy_overlap_count(local, remote), 0)

    def test_empty_identity_is_not_a_match(self):
        self.assertEqual(syncer.legacy_overlap_count([{}], [{}]), 0)


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
    def test_blocked_queue_preserves_other_files_and_replaces_rescanned_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            first = base / "downloads" / "first.xlsx"
            second = base / "downloads" / "second.xlsx"
            first.parent.mkdir()
            first.touch()
            second.touch()
            output = base / "upload"
            output.mkdir()
            queue = output / "blocked_rows_pending.csv"
            run = output / "blocked_rows_run.csv"

            def row(path, row_number, title):
                value = {field: "" for field in syncer.BLOCKED_FIELDS}
                value.update(Source_File_Path=str(path.resolve()),
                             Source_Row_Number__c=str(row_number),
                             Full_Title__c=title)
                return value

            with queue.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=syncer.BLOCKED_FIELDS)
                writer.writeheader()
                writer.writerows([row(first, 3, "旧第一条"), row(second, 4, "第二文件")])
            with run.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=syncer.BLOCKED_FIELDS)
                writer.writeheader()
                writer.writerow(row(first, 5, "新第一条"))

            count = syncer._merge_blocked_queue(queue, run, [first])
            with queue.open(newline="", encoding="utf-8") as stream:
                queued = list(csv.DictReader(stream))
            self.assertEqual(count, 2)
            self.assertEqual({item["Full_Title__c"] for item in queued},
                             {"新第一条", "第二文件"})

    def test_sync_reports_pending_blocked_rows_without_reprocessing_source_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            output, _, _ = syncer._state_paths(base, "zihao")
            output.mkdir(parents=True)
            queue = output / "blocked_rows_pending.csv"
            row = {field: "" for field in syncer.BLOCKED_FIELDS}
            row.update(Source_File_Path="source.xlsx", Source_Row_Number__c="4")
            with queue.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=syncer.BLOCKED_FIELDS)
                writer.writeheader()
                writer.writerow(row)

            result = syncer.sync(base, target_org="zihao")
            self.assertEqual(result["status"], "blocked_pending")
            self.assertEqual(result["blocked_rows"], 1)

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

    def test_schema_requires_all_fields_and_unique_external_id(self):
        fields = [{"name": name, "createable": True, "updateable": True} for name in
                  syncer.FIELDS]
        source = next(field for field in fields
                      if field["name"] == "Source_Key__c")
        source.update(externalId=True, unique=True)
        result = syncer.assess_target_schema({"name": "bidnews__c", "fields": fields})
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["not_createable_fields"], [])
        self.assertTrue(result["source_key_external_id"])
        self.assertTrue(result["source_key_unique"])

    def test_schema_reports_missing_field(self):
        fields = [{"name": name, "createable": True, "updateable": True} for name in
                  syncer.FIELDS
                  if name != "Full_Title__c"]
        result = syncer.assess_target_schema({"name": "bidnews__c", "fields": fields})
        self.assertEqual(result["missing_fields"], ["Full_Title__c"])

    def test_production_profile_uses_new_precise_amount_fields(self):
        fields = syncer.upload_fields("sfoa-prod")
        self.assertIn("Source_Budget__c", fields)
        self.assertIn("Source_Winning_Wan__c", fields)
        self.assertNotIn("Source_Budget_Wan__c", fields)
        self.assertIn("accountname__c", fields)
        self.assertIn("bidwinningname__c", fields)
        self.assertIn("website__c", fields)
        self.assertEqual(len(fields), len(set(fields)))

    def test_production_csv_contains_only_compact_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            source = output / "bidnews_upsert.csv"
            row = {name: "" for name in syncer.FIELDS}
            row.update(Source_Key__c="a" * 64, Name="标题",
                       Publication_Category__c="招标", Tender_Stage__c="招标中",
                       Buyer_Contact__c="测试联系人", Buyer_Phone__c="021-00000000",
                       Source_Budget_Wan__c="12.345",
                       Source_Winning_Wan__c="8.001")
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=syncer.FIELDS)
                writer.writeheader()
                writer.writerow(row)
            production = syncer.upload_csv(output, "sfoa-prod")
            with production.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(list(rows[0]), syncer.upload_fields("sfoa-prod"))
            self.assertEqual(rows[0]["Source_Budget__c"], "12.35")
            self.assertEqual(rows[0]["Source_Winning_Wan__c"], "8.001")
            for field in ("Publication_Category__c", "Tender_Stage__c",
                          "Buyer_Contact__c", "Buyer_Phone__c"):
                self.assertEqual(rows[0][field], row[field])

    @patch.object(syncer, "sf_query_all")
    def test_legacy_field_plan_only_fills_blank_new_fields(self, query):
        query.return_value = [{
            "Id": "a01", "Name": "旧标题", "Full_Title__c": None,
            "accountname__c": "旧采购单位", "Buyer_Name__c": "已有新值",
            "bidwinningname__c": "旧中标单位", "Winner_Name__c": "",
            "budget__c": 12, "Source_Budget__c": None,
            "bidwinningamount__c": 8, "Source_Winning_Wan__c": 9,
            "website__c": "https://old", "Announcement_Url_Full__c": None,
        }]
        with tempfile.TemporaryDirectory() as folder:
            result = syncer.prepare_legacy_field_backfill("sfoa-prod", Path(folder))
            with Path(result["backfill_csv"]).open(
                    newline="", encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(result["rows_to_update"], 1)
        self.assertEqual(row["Full_Title__c"], "旧标题")
        self.assertEqual(row["Buyer_Name__c"], "已有新值")
        self.assertEqual(row["Source_Budget__c"], "12")
        self.assertEqual(row["Source_Winning_Wan__c"], "9")

    def test_existing_external_ids_are_updates_not_inserts(self):
        result = syncer.compare_external_ids(
            ["a" * 64, "b" * 64, "c" * 64],
            ["a" * 64, "c" * 64], 0)
        self.assertEqual(result["existing_rows_to_update"], 2)
        self.assertEqual(result["new_rows_to_insert"], 1)
        self.assertTrue(result["safe_to_upsert"])

    def test_manual_rows_without_external_id_block_upload(self):
        result = syncer.compare_external_ids(["a" * 64], [], 3)
        self.assertEqual(result["remote_rows_without_key"], 3)
        self.assertFalse(result["safe_to_upsert"])

    def test_production_cutover_can_ignore_legacy_rows_without_key(self):
        result = syncer.compare_external_ids(
            ["a" * 64], [], 7188, ignore_remote_without_key=True)
        self.assertEqual(result["legacy_rows_ignored"], 7188)
        self.assertTrue(result["safe_to_upsert"])

    def test_initialize_production_cutover_marks_current_files_processed(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            downloads = base / "downloads"
            downloads.mkdir()
            (downloads / "old.xlsx").write_bytes(b"legacy")
            result = syncer.initialize_production_cutover(base)
            self.assertEqual(result["legacy_files_ignored"], 1)
            _, _, _, pending, _ = syncer._pending_files(
                base, target_org="sfoa-prod")
            self.assertEqual(pending, [])
            (downloads / "new.xlsx").write_bytes(b"new")
            _, _, _, pending, _ = syncer._pending_files(
                base, target_org="sfoa-prod")
            self.assertEqual([path.name for path in pending], ["new.xlsx"])

    def test_direct_production_sync_is_blocked_without_preflight(self):
        with patch.dict(syncer.os.environ, {}, clear=False):
            syncer.os.environ.pop("SFOA_PRODUCTION_PREFLIGHT_PASSED", None)
            with self.assertRaisesRegex(RuntimeError, "必须通过"):
                syncer.sync(Path(tempfile.gettempdir()), target_org="sfoa-prod")

    def test_legacy_rows_match_by_unique_url_or_composite(self):
        local = [
            {"Source_Key__c": "a" * 64, "swordfishwebsite__c": "https://jianyu/a",
             "website__c": "https://source/a", "release_date__c": "2025-01-01",
             "bidstate__c": "北京", "bidcity__c": "北京市", "type__c": "招标",
             "Name": "项目A", "projectnumber__c": "A-1"},
            {"Source_Key__c": "b" * 64, "swordfishwebsite__c": "https://jianyu/b",
             "website__c": "https://source/b", "release_date__c": "2025-01-02",
             "bidstate__c": "上海", "bidcity__c": "上海市", "type__c": "中标",
             "Name": "项目B", "projectnumber__c": "B-1"},
        ]
        remote = [
            {"Id": "001", **{key: value for key, value in local[0].items()
                                if key != "Source_Key__c"}},
            {"Id": "002", "release_date__c": "2025-01-02",
             "bidstate__c": "上海", "bidcity__c": "上海市", "type__c": "中标",
             "Name": "项目B", "projectnumber__c": "B-1"},
        ]
        matches, report = syncer.match_legacy_rows(local, remote)
        self.assertEqual({row["Source_Key__c"] for row in matches},
                         {"a" * 64, "b" * 64})
        self.assertEqual(report["safe_backfill_rows"], 2)

    def test_legacy_conflicting_identifiers_are_ambiguous(self):
        local = [
            {"Source_Key__c": "a" * 64, "swordfishwebsite__c": "https://jianyu/a",
             "website__c": "https://source/a"},
            {"Source_Key__c": "b" * 64, "swordfishwebsite__c": "https://jianyu/b",
             "website__c": "https://source/b"},
        ]
        remote = [{"Id": "001", "swordfishwebsite__c": "https://jianyu/a",
                   "website__c": "https://source/b"}]
        matches, report = syncer.match_legacy_rows(local, remote)
        self.assertEqual(matches, [])
        self.assertEqual(report["ambiguous_remote_rows"], 1)

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
