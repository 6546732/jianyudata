"""Incrementally upsert new Jianyu XLSX exports into the zihao Salesforce sandbox.

The manifest advances only after Bulk API 2.0 reports zero failed rows. Re-running
after an uncertain result is safe because Source_Key__c is a unique External ID.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from prepare_import import FIELDS, prepare

NODE = Path(os.environ.get("SFOA_NODE", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/node.exe"))
SF_CLI = Path(os.environ.get("SFOA_CLI", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/run.js"))
DEFAULT_BASE = Path(r"D:\桌面\data")
BULK_API_VERSION = os.environ.get("SFOA_API_VERSION", "67.0")


def _sf_environment() -> dict:
    environment = os.environ.copy()
    environment["SHELL"] = "powershell"
    # CLI 默认让所有进程写同一个每日 .sf 日志，并写本机遥测 ID。定时任务、
    # 手工查询或其他 Salesforce 自动化并发时会触发 Windows EPERM，导致命令
    # 在输出 JSON 前崩溃。自动任务不需要这两个文件。
    environment["SF_DISABLE_LOG_FILE"] = "true"
    environment["SF_DISABLE_TELEMETRY"] = "true"
    return environment


def sf_rest(target_org: str, endpoint: str, timeout: int = 180,
            attempts: int = 3) -> dict:
    """Call Salesforce REST through the authenticated CLI and return its body."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        process = subprocess.run(
            [str(NODE), str(SF_CLI), "api", "request", "rest", endpoint,
             "--target-org", target_org, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_sf_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            if not process.stdout.strip():
                raise RuntimeError(
                    "Salesforce REST 查询没有返回内容："
                    f"exit={process.returncode} stderr_chars={len(process.stderr)}")
            payload = json.loads(process.stdout)
            if process.returncode or payload.get("status") != 0:
                result = payload.get("result") or {}
                body = result.get("body") if isinstance(result, dict) else None
                nested_message = ""
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    nested_message = str(body[0].get("message") or
                                         body[0].get("errorCode") or "")
                elif isinstance(body, dict):
                    nested_message = str(body.get("message") or
                                         body.get("errorCode") or "")
                raise RuntimeError(
                    "Salesforce REST 查询失败："
                    f"exit={process.returncode} status={payload.get('status')} "
                    f"message={str(payload.get('message') or payload.get('name') or
                                   nested_message)[:500]}")
            result = payload.get("result") or {}
            body = result.get("body", result)
            if isinstance(body, str):
                body = json.loads(body)
            if not isinstance(body, dict):
                raise RuntimeError("Salesforce REST 查询缺少响应正文")
            return body
        except (json.JSONDecodeError, RuntimeError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise RuntimeError(
        f"Salesforce REST 查询连续{attempts}次失败：{last_error}") from last_error


def sf_query_all(target_org: str, soql: str) -> list[dict]:
    """Read every REST query page without writing to the target org."""
    endpoint = f"/services/data/v{BULK_API_VERSION}/query?q={quote(soql, safe='')}"
    records: list[dict] = []
    while endpoint:
        body = sf_rest(target_org, endpoint)
        page = body.get("records") or []
        if not isinstance(page, list):
            raise RuntimeError("Salesforce 查询返回的 records 不是列表")
        records.extend(page)
        endpoint = body.get("nextRecordsUrl") if not body.get("done", True) else None
    return records


def assess_target_schema(description: dict) -> dict:
    """Check every CSV field plus the external ID contract used by Bulk Upsert."""
    fields = {field.get("name"): field for field in description.get("fields") or []}
    missing = sorted(set(FIELDS) - set(fields))
    not_createable = sorted(
        name for name in FIELDS
        if name in fields and not fields[name].get("createable", False)
    )
    not_updateable = sorted(
        name for name in FIELDS
        if name in fields and not fields[name].get("updateable", False)
    )
    source_key = fields.get("Source_Key__c") or {}
    return {
        "object": description.get("name"),
        "missing_fields": missing,
        "not_createable_fields": not_createable,
        "not_updateable_fields": not_updateable,
        "source_key_external_id": bool(source_key.get("externalId")),
        "source_key_unique": bool(source_key.get("unique")),
    }


def compare_external_ids(local_keys: list[str], remote_keys: list[str],
                         remote_without_key: int) -> dict:
    """Show what an upsert would insert/update and detect unsafe remote state."""
    local_counts = Counter(local_keys)
    remote_counts = Counter(key for key in remote_keys if key)
    duplicate_local = sorted(key for key, count in local_counts.items() if count > 1)
    duplicate_remote = sorted(key for key, count in remote_counts.items() if count > 1)
    local = set(local_counts)
    remote = set(remote_counts)
    return {
        "local_rows": len(local_keys),
        "local_unique_keys": len(local),
        "existing_rows_to_update": len(local & remote),
        "new_rows_to_insert": len(local - remote),
        "remote_rows_with_key": len(remote_keys),
        "remote_unique_keys": len(remote),
        "remote_rows_without_key": int(remote_without_key),
        "duplicate_local_keys": len(duplicate_local),
        "duplicate_remote_keys": len(duplicate_remote),
        "safe_to_upsert": not duplicate_local and not duplicate_remote
                          and int(remote_without_key) == 0,
    }


def _unique_index(rows: list[dict], key_function) -> dict[object, str]:
    grouped: dict[object, set[str]] = {}
    for row in rows:
        value = key_function(row)
        source_key = str(row.get("Source_Key__c") or "")
        if value and source_key:
            grouped.setdefault(value, set()).add(source_key)
    return {value: next(iter(keys)) for value, keys in grouped.items()
            if len(keys) == 1}


def match_legacy_rows(local_rows: list[dict], remote_rows: list[dict]) -> tuple[list[dict], dict]:
    """Match old production rows only when strong identifiers agree uniquely."""
    def text(row, name):
        return str(row.get(name) or "").strip()

    def composite(row):
        values = tuple(text(row, name) for name in (
            "release_date__c", "bidstate__c", "bidcity__c", "type__c",
            "Name", "projectnumber__c"))
        return values if all(values[:5]) else None

    def composite_without_number(row):
        values = tuple(text(row, name) for name in (
            "release_date__c", "bidstate__c", "bidcity__c", "type__c", "Name"))
        return values if all(values) else None

    indexes = {
        "swordfishwebsite__c": _unique_index(
            local_rows, lambda row: text(row, "swordfishwebsite__c") or None),
        "website__c": _unique_index(
            local_rows, lambda row: text(row, "website__c") or None),
        "content__c": _unique_index(
            local_rows, lambda row: text(row, "content__c") or None),
        "legacy_composite": _unique_index(local_rows, composite),
        "legacy_composite_without_number": _unique_index(
            local_rows, composite_without_number),
    }
    preliminary: list[dict] = []
    ambiguous = 0
    unmatched = 0
    for remote in remote_rows:
        candidates: dict[str, str] = {}
        for method, value in (
                ("swordfishwebsite__c", text(remote, "swordfishwebsite__c") or None),
                ("website__c", text(remote, "website__c") or None),
                ("content__c", text(remote, "content__c") or None),
                ("legacy_composite", composite(remote)),
                ("legacy_composite_without_number", composite_without_number(remote))):
            source_key = indexes[method].get(value) if value else None
            if source_key:
                candidates[method] = source_key
        unique = set(candidates.values())
        if len(unique) == 1:
            preliminary.append({
                "Id": text(remote, "Id"),
                "Source_Key__c": next(iter(unique)),
                "Match_Methods": ",".join(sorted(candidates)),
            })
        elif len(unique) > 1:
            ambiguous += 1
        else:
            unmatched += 1
    by_key = Counter(row["Source_Key__c"] for row in preliminary)
    collisions = sum(1 for count in by_key.values() if count > 1)
    matched = [row for row in preliminary if by_key[row["Source_Key__c"]] == 1]
    colliding_rows = sum(count for count in by_key.values() if count > 1)
    method_counts = Counter()
    for row in matched:
        method_counts.update(row["Match_Methods"].split(","))
    report = {
        "remote_rows_total": len(remote_rows),
        "matched_for_source_key_backfill": len(matched),
        "unmatched_remote_rows": unmatched,
        "ambiguous_remote_rows": ambiguous,
        "colliding_source_keys": collisions,
        "colliding_remote_rows": colliding_rows,
        "match_method_counts": dict(method_counts),
        "safe_backfill_rows": len(matched),
    }
    return matched, report


def prepare_legacy_backfill(local_rows: list[dict], target_org: str,
                            output: Path) -> dict:
    """Create an Id-to-Source_Key plan; this function never updates Salesforce."""
    fields = ("Id,Name,bidstate__c,bidcity__c,type__c,release_date__c,"
              "website__c,swordfishwebsite__c,projectnumber__c,content__c")
    remote_rows = sf_query_all(target_org, f"SELECT {fields} FROM bidnews__c")
    matches, report = match_legacy_rows(local_rows, remote_rows)
    path = output / "production_source_key_backfill.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["Id", "Source_Key__c", "Match_Methods"],
            lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(matches)
    report["backfill_csv"] = str(path)
    return report


def dry_run(base: Path = DEFAULT_BASE, target_org: str = "sfoa-prod",
            force: bool = False) -> dict:
    """Prepare pending files and perform a read-only production preflight."""
    base = base.resolve()
    output, _, _, pending, _ = _pending_files(base, force, target_org)
    if not pending:
        result = {
            "status": "unchanged", "target_org": target_org,
            "files": 0, "rows": 0, "safe_to_execute": True,
        }
    else:
        report = prepare(base / "downloads", output, selected_files=pending)
        if report["warnings"]:
            raise RuntimeError("导入预检查存在异常：" +
                               json.dumps(report["warnings"], ensure_ascii=False))
        csv_path = output / "bidnews_upsert.csv"
        with csv_path.open(newline="", encoding="utf-8") as stream:
            local_rows = list(csv.DictReader(stream))
        local_keys = [row["Source_Key__c"] for row in local_rows]
        description = sf_rest(
            target_org, f"/services/data/v{BULK_API_VERSION}/sobjects/bidnews__c/describe")
        schema = assess_target_schema(description)
        schema_ok = (
            schema["object"] == "bidnews__c"
            and not schema["missing_fields"]
            and not schema["not_createable_fields"]
            and not schema["not_updateable_fields"]
            and schema["source_key_external_id"]
            and schema["source_key_unique"]
        )
        if "Source_Key__c" in schema["missing_fields"]:
            legacy = prepare_legacy_backfill(local_rows, target_org, output)
            comparison = {
                "status": "not_checked",
                "reason": "生产缺少 Source_Key__c，无法把手工记录与本地记录安全对应",
                "local_rows": len(local_keys),
                **legacy,
                "safe_to_upsert": False,
            }
        else:
            remote_records = sf_query_all(
                target_org,
                "SELECT Source_Key__c FROM bidnews__c WHERE Source_Key__c != null")
            remote_keys = [str(row.get("Source_Key__c") or "") for row in remote_records]
            without_key = sf_rest(
                target_org,
                f"/services/data/v{BULK_API_VERSION}/query?q=" +
                quote("SELECT COUNT() FROM bidnews__c WHERE Source_Key__c = null", safe="")
            ).get("totalSize", 0)
            comparison = compare_external_ids(local_keys, remote_keys, int(without_key))
        safe = schema_ok and comparison["safe_to_upsert"]
        result = {
            "status": "ready" if safe else "blocked",
            "target_org": target_org,
            "files": len(pending),
            "rows": report["rows_written"],
            "duplicates_in_batch": report["duplicates_skipped"],
            "categories_skipped": report["categories_skipped"],
            "schema": schema,
            "comparison": comparison,
            "safe_to_execute": safe,
        }
    (output / "production_preflight.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _salesforce_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)


def validate_bulk_job(job: dict, expected_rows: int) -> dict:
    """Require an exact, successful bidnews upsert before advancing the manifest."""
    processed = int(job.get("numberRecordsProcessed", -1))
    failed = int(job.get("numberRecordsFailed", -1))
    valid = (
        job.get("object") == "bidnews__c"
        and job.get("operation") == "upsert"
        and job.get("externalIdFieldName") == "Source_Key__c"
        and job.get("state") == "JobComplete"
        and processed == expected_rows
        and failed == 0
    )
    if not valid:
        raise RuntimeError(
            "Salesforce Bulk Job 未通过核验："
            f"job={job.get('id')} object={job.get('object')} "
            f"operation={job.get('operation')} state={job.get('state')} "
            f"processed={processed}/{expected_rows} failed={failed}")
    return job


def get_bulk_job(target_org: str, job_id: str) -> dict:
    endpoint = f"/services/data/v{BULK_API_VERSION}/jobs/ingest/{job_id}"
    return sf_rest(target_org, endpoint)


def find_recent_bulk_job(target_org: str, started_at: datetime,
                         expected_rows: int, attempts: int = 6) -> dict:
    """Find the job created by this run when the upsert command loses stdout."""
    endpoint = f"/services/data/v{BULK_API_VERSION}/jobs/ingest"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            records = sf_rest(target_org, endpoint).get("records") or []
            candidates = []
            for job in records:
                try:
                    created_at = _salesforce_time(job.get("createdDate", ""))
                except (TypeError, ValueError):
                    continue
                if (created_at >= started_at
                        and job.get("object") == "bidnews__c"
                        and job.get("operation") == "upsert"
                        and job.get("externalIdFieldName") == "Source_Key__c"):
                    candidates.append((created_at, job))
            for _, summary in sorted(candidates, reverse=True,
                                     key=lambda item: item[0]):
                detail = get_bulk_job(target_org, summary["id"])
                if detail.get("state") in {"Open", "UploadComplete", "InProgress"}:
                    continue
                if int(detail.get("numberRecordsProcessed", -1)) == expected_rows:
                    return validate_bulk_job(detail, expected_rows)
        except (RuntimeError, subprocess.SubprocessError) as error:
            last_error = error
        if attempt + 1 < attempts:
            time.sleep(5)
    message = "找不到与本次上传时间、对象、唯一键及条数完全匹配的 Bulk Job"
    if last_error:
        message += f"；最后一次查询错误：{last_error}"
    raise RuntimeError(message)


def verify_recent_upsert(csv_path: Path, target_org: str, started_at: datetime,
                         expected_rows: int) -> bool:
    """Confirm every external ID was modified by this run if Bulk CLI loses its JSON."""
    with csv_path.open(newline="", encoding="utf-8") as stream:
        keys = [row["Source_Key__c"] for row in csv.DictReader(stream)]
    if (len(keys) != expected_rows or len(set(keys)) != expected_rows
            or not all(re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)):
        return False
    since = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    for offset in range(0, len(keys), 100):
        batch = keys[offset:offset + 100]
        quoted = ",".join(f"'{key}'" for key in batch)
        soql = ("SELECT COUNT() FROM bidnews__c WHERE "
                f"Source_Key__c IN ({quoted}) AND LastModifiedDate >= {since}")
        query = subprocess.run(
            [str(NODE), str(SF_CLI), "data", "query", "--target-org", target_org,
             "--query", soql, "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, env=_sf_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            result = json.loads(query.stdout)
        except json.JSONDecodeError:
            return False
        if (query.returncode or result.get("status") != 0
                or (result.get("result") or {}).get("totalSize") != len(batch)):
            return False
    return True


def digest(path: Path) -> str:
    hash_object = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hash_object.update(chunk)
    return hash_object.hexdigest()


def _write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _state_paths(base: Path, target_org: str) -> tuple[Path, Path, Path]:
    """Keep upload output, manifest and lock isolated for each Salesforce org."""
    if target_org == "zihao":
        # Preserve the existing sandbox state and scheduled-task paths.
        return (base / "sfoa_upload", base / "sfoa_sync_manifest.json",
                base / "sfoa_sync.lock")
    org_key = re.sub(r"[^A-Za-z0-9._-]+", "_", target_org).strip("._-")
    if not org_key:
        raise ValueError("Salesforce 组织别名不能为空")
    return (base / f"sfoa_upload_{org_key}",
            base / f"sfoa_sync_manifest_{org_key}.json",
            base / f"sfoa_sync_{org_key}.lock")


def _pending_files(base: Path, force: bool = False,
                   target_org: str = "zihao") -> tuple[Path, Path, dict, list[Path], dict]:
    source = base / "downloads"
    output, manifest_path, _ = _state_paths(base, target_org)
    output.mkdir(parents=True, exist_ok=True)
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {"version": 1, "files": {}})
    files = sorted(p for p in source.rglob("*.xlsx") if not p.name.startswith("~$"))
    hashes = {str(p.resolve()): digest(p) for p in files}
    pending = [p for p in files if force or
               manifest["files"].get(str(p.resolve())) != hashes[str(p.resolve())]]
    return output, manifest_path, manifest, pending, hashes


@contextmanager
def single_instance(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.seek(0)
            stream.write(b"1")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("已有 Salesforce 同步任务运行") from error
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def sync(base: Path = DEFAULT_BASE, target_org: str = "zihao", force: bool = False) -> dict:
    if (target_org == "sfoa-prod"
            and os.environ.get("SFOA_PRODUCTION_PREFLIGHT_PASSED") != "1"):
        raise RuntimeError(
            "生产上传必须通过 upload_production.ps1 的只读预演；"
            "禁止直接调用 sync_to_salesforce.py 写入 sfoa-prod")
    base = base.resolve()
    _, _, lock_path = _state_paths(base, target_org)
    with single_instance(lock_path):
        output, manifest_path, manifest, pending, hashes = _pending_files(
            base, force, target_org)
        if not pending:
            return {"status": "unchanged", "files": 0, "rows": 0,
                    "target_org": target_org,
                    "environment": "生产" if target_org == "sfoa-prod" else "沙盒",
                    "successful_rows": 0, "failed_rows": 0,
                    "inserted_rows": 0, "updated_rows": 0,
                    "duplicates_filtered": 0, "categories_filtered": 0}
        report = prepare(base / "downloads", output, selected_files=pending)
        if report["warnings"]:
            raise RuntimeError("导入预检查存在异常，未上传：" + json.dumps(report["warnings"], ensure_ascii=False))
        if report["rows_written"]:
            # Count inserts and updates before the upsert. Source_Key__c is unique,
            # so this read-only comparison is exact for this batch.
            with (output / "bidnews_upsert.csv").open(
                    newline="", encoding="utf-8") as stream:
                local_keys = [row["Source_Key__c"] for row in csv.DictReader(stream)]
            remote_records = sf_query_all(
                target_org,
                "SELECT Source_Key__c FROM bidnews__c WHERE Source_Key__c != null")
            remote_keys = {str(row.get("Source_Key__c") or "")
                           for row in remote_records}
            inserted_rows = sum(key not in remote_keys for key in local_keys)
            updated_rows = len(local_keys) - inserted_rows
            # 在创建写入型 Bulk Job 前确认认证和网络可用；预检只读且可安全重试。
            sf_rest(target_org, f"/services/data/v{BULK_API_VERSION}/limits")
            command = [
                str(NODE), str(SF_CLI), "data", "upsert", "bulk",
                "--target-org", target_org,
                "--sobject", "bidnews__c",
                "--external-id", "Source_Key__c",
                "--file", str((output / "bidnews_upsert.csv").resolve()),
                "--line-ending", "CRLF", "--wait", "10", "--json",
            ]
            started_at = datetime.now(timezone.utc)
            process = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, env=_sf_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            verified_without_cli_result = False
            try:
                result = json.loads(process.stdout)
            except json.JSONDecodeError as error:
                try:
                    job = find_recent_bulk_job(target_org, started_at,
                                               report["rows_written"])
                    job_id = job["id"]
                    reconciliation_method = "bulk_job_rest_lookup"
                except RuntimeError as lookup_error:
                    if not verify_recent_upsert(output / "bidnews_upsert.csv", target_org,
                                                started_at, report["rows_written"]):
                        raise RuntimeError(
                            "Salesforce CLI 未返回 JSON，且 REST Job 与外部键核验均失败；"
                            f"exit={process.returncode} stderr_chars={len(process.stderr)}；"
                            f"REST={lookup_error}；未推进同步进度") from error
                    job_id = None
                    reconciliation_method = "all_external_ids_modified_after_bulk_submit"
                verified_without_cli_result = True
            else:
                job = (result.get("result") or {}).get("jobInfo") or {}
                processed = int(job.get("numberRecordsProcessed", -1))
                failed = int(job.get("numberRecordsFailed", -1))
                if (process.returncode or result.get("status") != 0
                        or job.get("state") != "JobComplete"
                        or processed != report["rows_written"] or failed != 0):
                    raise RuntimeError(
                        f"Salesforce 批量上传未通过核验：job={job.get('id')} "
                        f"state={job.get('state')} processed={processed} failed={failed}；"
                        "未推进同步进度，请检查 Bulk Job"
                    )
                job_id = job["id"]
        else:
            job_id = None
            verified_without_cli_result = False
            inserted_rows = 0
            updated_rows = 0
        for path in pending:
            manifest["files"][str(path.resolve())] = hashes[str(path.resolve())]
        manifest["last_job_id"] = job_id
        manifest["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        if verified_without_cli_result:
            manifest["last_reconciliation"] = {
                "method": reconciliation_method,
                "files": len(pending), "rows": report["rows_written"],
                "verified_at": manifest["last_synced_at"],
            }
        _write_manifest(manifest_path, manifest)
        return {"status": "uploaded", "files": len(pending),
                "rows": report["rows_written"], "job_id": job_id,
                "target_org": target_org,
                "environment": "生产" if target_org == "sfoa-prod" else "沙盒",
                "successful_rows": report["rows_written"], "failed_rows": 0,
                "inserted_rows": inserted_rows, "updated_rows": updated_rows,
                "verified_without_cli_result": verified_without_cli_result,
                "duplicates_in_batch": report["duplicates_skipped"],
                "duplicates_filtered": report["duplicates_skipped"],
                "categories_filtered": (
                    sum(report["categories_skipped"].values())
                    if isinstance(report["categories_skipped"], dict)
                    else int(report["categories_skipped"] or 0))}


def reconcile(base: Path, target_org: str, job_id: str) -> dict:
    """Advance a stale manifest after independently proving a completed Bulk Job."""
    base = base.resolve()
    _, _, lock_path = _state_paths(base, target_org)
    with single_instance(lock_path):
        output, manifest_path, manifest, pending, hashes = _pending_files(
            base, target_org=target_org)
        if not pending:
            return {"status": "unchanged", "files": 0, "rows": 0,
                    "job_id": manifest.get("last_job_id")}
        report = prepare(base / "downloads", output, selected_files=pending)
        if report["warnings"]:
            raise RuntimeError("恢复预检查存在异常：" +
                               json.dumps(report["warnings"], ensure_ascii=False))
        job = validate_bulk_job(get_bulk_job(target_org, job_id), report["rows_written"])
        for path in pending:
            manifest["files"][str(path.resolve())] = hashes[str(path.resolve())]
        now = datetime.now(timezone.utc).isoformat()
        manifest["last_job_id"] = job["id"]
        manifest["last_synced_at"] = now
        manifest["last_reconciliation"] = {
            "method": "explicit_bulk_job_rest_lookup",
            "files": len(pending), "rows": report["rows_written"],
            "verified_at": now,
        }
        _write_manifest(manifest_path, manifest)
        return {"status": "reconciled", "files": len(pending),
                "rows": report["rows_written"], "job_id": job["id"],
                "failed": int(job["numberRecordsFailed"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--target-org", default="zihao")
    parser.add_argument("--force", action="store_true", help="安全地按唯一键重传全部文件以补字段")
    parser.add_argument("--dry-run", action="store_true",
                        help="只读核对字段和外部键，统计新增/更新条数，不上传")
    parser.add_argument("--reconcile-job", help="核验已完成的 Bulk Job 并恢复未推进的本地同步清单")
    args = parser.parse_args()
    if args.dry_run and args.reconcile_job:
        parser.error("--dry-run 与 --reconcile-job 不能同时使用")
    action = (dry_run(args.base, args.target_org, args.force) if args.dry_run else
              reconcile(args.base, args.target_org, args.reconcile_job)
              if args.reconcile_job else sync(args.base, args.target_org, args.force))
    print(json.dumps(action, ensure_ascii=False, indent=2))
