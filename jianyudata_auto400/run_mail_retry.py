"""Retry queued email without running download or Salesforce upload."""
import json
from datetime import datetime
from pathlib import Path

from mail_delivery import BASE, send_due


def main():
    logs = BASE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    results = send_due()
    summary = {
        "run_at": started.isoformat(),
        "checked": len(results),
        "sent": sum(item.get("status") == "sent" for item in results),
        "pending": sum(item.get("status") == "pending" for item in results),
        "permanently_failed": sum(item.get("status") == "permanently_failed"
                                  for item in results),
        "message_ids": [item.get("id") for item in results],
    }
    path = logs / f"mail-retry-{started:%Y%m%d}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if summary["permanently_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
