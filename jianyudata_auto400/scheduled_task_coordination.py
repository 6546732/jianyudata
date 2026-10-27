"""Coordinate scheduled jobs that may be backfilled at the same time after standby."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path


DOWNLOAD_TASK = "Jianyu-Formal-1030"
UPLOAD_TASK = "Jianyu-Salesforce-Sync-1100"


def scheduled_task_state(name: str) -> str:
    """Return the Windows Task Scheduler state without relying on localized output."""
    escaped = name.replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        f"(Get-ScheduledTask -TaskName '{escaped}').State.ToString()"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"无法读取计划任务 {name} 的状态：{detail or completed.returncode}")
    return completed.stdout.strip()


def wait_for_tasks(
    names,
    *,
    timeout: float = 3 * 60 * 60,
    startup_grace: float = 10,
    poll: float = 15,
    state_reader=scheduled_task_state,
    sleeper=time.sleep,
    clock=time.monotonic,
) -> dict[str, str]:
    """Wait for tasks that are running, including tasks starting in the same backfill wave."""
    names = tuple(dict.fromkeys(str(name) for name in names))
    started = clock()
    states = {name: state_reader(name) for name in names}

    # After a machine wakes, missed tasks are launched within a few seconds of one
    # another. Recheck once before deciding that another task is not running.
    if names and not any(state.casefold() == "running" for state in states.values()):
        sleeper(max(0, startup_grace))
        states = {name: state_reader(name) for name in names}

    while any(state.casefold() == "running" for state in states.values()):
        if clock() - started >= timeout:
            running = ", ".join(
                name for name, state in states.items() if state.casefold() == "running"
            )
            raise TimeoutError(f"等待计划任务结束超时：{running}")
        sleeper(max(0.1, min(poll, timeout - (clock() - started))))
        states = {name: state_reader(name) for name in names}
    return states

