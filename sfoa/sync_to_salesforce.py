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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from prepare_import import prepare

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
                raise RuntimeError(
                    "Salesforce REST 查询失败："
                    f"exit={process.returncode} status={payload.get('status')} "
                    f"message={str(payload.get('message') or payload.get('name') or '')[:200]}")
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
    base = base.resolve()
    _, _, lock_path = _state_paths(base, target_org)
    with single_instance(lock_path):
        output, manifest_path, manifest, pending, hashes = _pending_files(
            base, force, target_org)
        if not pending:
            return {"status": "unchanged", "files": 0, "rows": 0}
        report = prepare(base / "downloads", output, selected_files=pending)
        if report["warnings"]:
            raise RuntimeError("导入预检查存在异常，未上传：" + json.dumps(report["warnings"], ensure_ascii=False))
        if report["rows_written"]:
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
                "verified_without_cli_result": verified_without_cli_result,
                "duplicates_in_batch": report["duplicates_skipped"]}


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
    parser.add_argument("--reconcile-job", help="核验已完成的 Bulk Job 并恢复未推进的本地同步清单")
    args = parser.parse_args()
    action = (reconcile(args.base, args.target_org, args.reconcile_job)
              if args.reconcile_job else sync(args.base, args.target_org, args.force))
    print(json.dumps(action, ensure_ascii=False, indent=2))
