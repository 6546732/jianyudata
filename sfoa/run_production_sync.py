"""Safe production preflight + upload with a standard daily summary file."""
import argparse
import json
import os
import traceback
from datetime import datetime
from pathlib import Path

from sync_to_salesforce import dry_run, sync

BASE = Path(r"D:\桌面\data")
LOGS = BASE / "logs"


def count_value(value):
    return sum(int(item or 0) for item in value.values()) if isinstance(value, dict) else int(value or 0)


def main(force=False):
    LOGS.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    result = None
    error = ""
    preflight = None
    try:
        preflight = dry_run(BASE, "sfoa-prod", force)
        if not preflight.get("safe_to_execute"):
            raise RuntimeError("生产预演未通过，已阻止写入")
        os.environ["SFOA_PRODUCTION_PREFLIGHT_PASSED"] = "1"
        result = sync(BASE, "sfoa-prod", force)
    except Exception:
        error = traceback.format_exc()
    details = result or {}
    success = details.get("status") in {"uploaded", "unchanged", "reconciled"}
    summary = {
        "run_started_at": started.isoformat(),
        "run_finished_at": datetime.now().isoformat(),
        "target_org": "sfoa-prod",
        "environment": "生产",
        "status": "成功" if success else "失败",
        "attempted_rows": int((preflight or {}).get("rows") or 0),
        "uploaded_rows": int(details.get("rows") or 0) if success else 0,
        "successful_rows": int(details.get("successful_rows", details.get("rows", 0)) or 0) if success else 0,
        "failed_rows": int(details.get("failed_rows") or 0) if success else int((preflight or {}).get("rows") or 0),
        "inserted_rows": details.get("inserted_rows"),
        "updated_rows": details.get("updated_rows"),
        "duplicates_filtered": count_value(details.get("duplicates_filtered", (preflight or {}).get("duplicates_in_batch", 0))),
        "categories_filtered": count_value(details.get("categories_filtered", (preflight or {}).get("categories_skipped", 0))),
        "job_id": details.get("job_id"),
        "details": details,
        "preflight": preflight,
        "failure_reason": error.splitlines()[-1][:1200] if error else "",
    }
    path = LOGS / f"salesforce-production-{stamp}-summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(args.force))
