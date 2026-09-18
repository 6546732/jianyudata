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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from prepare_import import prepare

NODE = Path(os.environ.get("SFOA_NODE", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/node.exe"))
SF_CLI = Path(os.environ.get("SFOA_CLI", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/run.js"))
DEFAULT_BASE = Path(r"D:\桌面\data")


def verify_recent_upsert(csv_path: Path, target_org: str, started_at: datetime,
                         expected_rows: int) -> bool:
    """Confirm every external ID was modified by this run if Bulk CLI loses its JSON."""
    with csv_path.open(newline="", encoding="utf-8") as stream:
        keys = [row["Source_Key__c"] for row in csv.DictReader(stream)]
    if (len(keys) != expected_rows or len(set(keys)) != expected_rows
            or not all(re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)):
        return False
    since = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    environment = os.environ.copy()
    environment["SHELL"] = "powershell"
    for offset in range(0, len(keys), 100):
        batch = keys[offset:offset + 100]
        quoted = ",".join(f"'{key}'" for key in batch)
        soql = ("SELECT COUNT() FROM bidnews__c WHERE "
                f"Source_Key__c IN ({quoted}) AND LastModifiedDate >= {since}")
        query = subprocess.run(
            [str(NODE), str(SF_CLI), "data", "query", "--target-org", target_org,
             "--query", soql, "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, env=environment,
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
    source = base / "downloads"
    output = base / "sfoa_upload"
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = base / "sfoa_sync_manifest.json"
    with single_instance(base / "sfoa_sync.lock"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"version": 1, "files": {}}
        files = sorted(p for p in source.rglob("*.xlsx") if not p.name.startswith("~$"))
        hashes = {str(p.resolve()): digest(p) for p in files}
        pending = [p for p in files if force or
                   manifest["files"].get(str(p.resolve())) != hashes[str(p.resolve())]]
        if not pending:
            return {"status": "unchanged", "files": 0, "rows": 0}
        report = prepare(source, output, selected_files=pending)
        if report["warnings"]:
            raise RuntimeError("导入预检查存在异常，未上传：" + json.dumps(report["warnings"], ensure_ascii=False))
        if report["rows_written"]:
            command = [
                str(NODE), str(SF_CLI), "data", "upsert", "bulk",
                "--target-org", target_org,
                "--sobject", "bidnews__c",
                "--external-id", "Source_Key__c",
                "--file", str((output / "bidnews_upsert.csv").resolve()),
                "--line-ending", "CRLF", "--wait", "10", "--json",
            ]
            environment = os.environ.copy()
            environment["SHELL"] = "powershell"
            started_at = datetime.now(timezone.utc)
            process = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            verified_without_cli_result = False
            try:
                result = json.loads(process.stdout)
            except json.JSONDecodeError as error:
                if not verify_recent_upsert(output / "bidnews_upsert.csv", target_org,
                                            started_at, report["rows_written"]):
                    raise RuntimeError(
                        "Salesforce CLI 未返回 JSON，且无法核实本次所有外部键均已更新；"
                        f"exit={process.returncode} stderr_chars={len(process.stderr)}；"
                        "未推进同步进度") from error
                job_id = None
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
                "method": "all_external_ids_modified_after_bulk_submit",
                "files": len(pending), "rows": report["rows_written"],
                "verified_at": manifest["last_synced_at"],
            }
        temporary = manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
        return {"status": "uploaded", "files": len(pending),
                "rows": report["rows_written"], "job_id": job_id,
                "verified_without_cli_result": verified_without_cli_result,
                "duplicates_in_batch": report["duplicates_skipped"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--target-org", default="zihao")
    parser.add_argument("--force", action="store_true", help="安全地按唯一键重传全部文件以补字段")
    args = parser.parse_args()
    print(json.dumps(sync(args.base, args.target_org, args.force), ensure_ascii=False, indent=2))
