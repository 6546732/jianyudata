"""Open Jianyu in the dedicated headless Chrome and verify login; never export."""
from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path

from auto_login import PasswordLogin
from browser_session import connect_browser
from export_one_day import FILTER_URL, OneDay
from nightly_job import load_login

ROOT = Path(__file__).resolve().parent
BASE = Path(r"D:\桌面\data")
LOGS = BASE / "logs"


def chrome_path() -> Path:
    candidates = [Path(os.environ.get(name, "")) / "Google/Chrome/Application/chrome.exe"
                  for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    value = next((path for path in candidates if path.is_file()), None)
    if value is None:
        raise FileNotFoundError("未找到 Google Chrome")
    return value


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone()
    log = LOGS / f"headless-smoke-{started:%Y%m%d-%H%M%S}.json"
    result = {"started_at": started.isoformat(), "action": "login_check_only",
              "export_attempted": False, "success": False}
    try:
        driver = connect_browser(chrome_path(), BASE)
        username, password = load_login()
        PasswordLogin(username, password, BASE).ensure(driver)
        job = OneDay(driver, "2025-01-03", BASE, False)
        job.find_page(lambda: "筛选日期" in job.body() and
                      "关键词匹配方式" in job.body(), timeout=30)
        result.update(success=True, title=driver.title, url=driver.current_url,
                      filter_ready=True)
        return 0
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error),
                      traceback=traceback.format_exc())
        return 1
    finally:
        result["finished_at"] = datetime.now().astimezone().isoformat()
        log.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
