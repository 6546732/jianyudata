"""Independent 10:50/11:20 watchdogs and the single combined daily report."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from mail_delivery import BASE, enqueue_email
from scheduled_task_coordination import DOWNLOAD_TASK, UPLOAD_TASK, wait_for_tasks

LOGS = BASE / "logs"


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def same_day(summary: dict, day: str) -> bool:
    return str(summary.get("run_started_at") or "")[:10] == day


def latest_summary(day: str, pattern: str, exclude=()) -> tuple[Path | None, dict]:
    candidates = []
    for path in LOGS.glob(pattern):
        if any(token in path.name for token in exclude):
            continue
        value = read(path)
        if same_day(value, day):
            candidates.append((path.stat().st_mtime, path, value))
    if not candidates:
        return None, {}
    _, path, value = max(candidates, key=lambda item: item[0])
    return path, value


def upload_section(label: str, summary: dict, expected: bool) -> list[str]:
    if not summary:
        return [f"{label}：{'未生成汇总（异常）' if expected else '今日未执行'}"]
    return [
        f"{label}：{summary.get('status', '未知')}",
        f"  尝试：{summary.get('attempted_rows', 0) if summary.get('attempted_rows') is not None else '未知'} 条",
        f"  成功：{summary.get('successful_rows', summary.get('uploaded_rows', 0))} 条",
        f"  失败：{summary.get('failed_rows', 0)} 条",
        f"  超长正文隔离待处理：{summary.get('blocked_rows', 0)} 条",
        f"  插入：{summary.get('inserted_rows', '未知')} 条",
        f"  更新：{summary.get('updated_rows', '未知')} 条",
        f"  批内重复过滤：{summary.get('duplicates_filtered', 0)} 条",
        f"  非目标类别过滤：{summary.get('categories_filtered', 0)} 条",
        f"  Bulk Job：{summary.get('job_id') or '无'}",
        *((["  失败原因：" + str(summary.get("failure_reason"))]
           if summary.get("failure_reason") else [])),
        *((["  完整原文队列：" + str(summary.get("blocked_rows_file"))]
           if summary.get("blocked_rows_file") else [])),
    ]


def combined_report(day: str) -> tuple[str, str, bool]:
    _, download = latest_summary(day, "*-summary.json",
                                 exclude=("salesforce-", "watchdog-"))
    _, sandbox = latest_summary(day, "salesforce-*-summary.json",
                                exclude=("salesforce-production-",))
    _, production = latest_summary(day, "salesforce-production-*-summary.json")
    export = download.get("export") or {}
    download_ok = bool(download) and download.get("run_status") == "成功"
    sandbox_ok = bool(sandbox) and sandbox.get("status") == "成功"
    production_state = (production.get("status") if production else "未执行")
    environment = "沙盒+生产" if production else "沙盒（生产未执行）"
    downloaded = int(export.get("rows") or 0)
    uploaded = int(sandbox.get("successful_rows", sandbox.get("uploaded_rows", 0)) or 0)
    production_ok = bool(production) and production.get("status") == "成功"
    result = "成功" if download_ok and sandbox_ok and production_ok else "异常"
    subject = (f"[剑鱼自动化][{environment}] {day} 下载{downloaded}条 / "
               f"沙盒上传{uploaded}条 / 生产上传{production.get('successful_rows', 0)}条 / {result}")
    completed = export.get("completed_date_start") or "未知"
    completed_end = export.get("completed_date_end") or completed
    range_text = completed if completed == completed_end else f"{completed} 至 {completed_end}"
    lines = [
        f"剑鱼标讯每日合并汇总（{day}）",
        "",
        "【下载】",
        f"执行结果：{download.get('run_status', '未生成汇总')}",
        f"本次完成日期范围：{range_text}",
        f"下载文件：{export.get('batches', 0)} 个",
        f"下载数量：{downloaded} 条",
        f"本地校验：{export.get('local_status', '未知')}，核验 {export.get('local_verified_rows', 0)} 条",
        f"当前进度：{export.get('progress_date') or '未知'}；状态：{export.get('progress_status') or '未知'}",
        f"今日剩余额度：{export.get('remaining_quota') if export.get('remaining_quota') is not None else '未知'} 条",
    ]
    if download.get("failure_reason"):
        lines.append("失败原因：" + str(download["failure_reason"]))
    lines.extend(["", "【上传】"])
    lines.extend(upload_section("沙盒 zihao", sandbox, expected=True))
    lines.append("")
    lines.extend(upload_section("生产 sfoa-prod", production, expected=True))
    lines.extend(["", f"目标环境检测：沙盒={sandbox.get('status', '未生成')}；生产={production_state}"])
    return subject, "\n".join(lines) + "\n", download_ok and sandbox_ok and production_ok


def record_watchdog(mode: str, day: str, status: str, details: dict) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    value = {"run_started_at": datetime.now().isoformat(), "mode": mode,
             "day": day, "status": status, **details}
    (LOGS / f"watchdog-{day}-{mode}.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(mode: str, day: str | None = None) -> int:
    day = day or date.today().isoformat()
    LOGS.mkdir(parents=True, exist_ok=True)
    wait_error = ""
    if day == date.today().isoformat():
        task_names = ((DOWNLOAD_TASK,) if mode == "download"
                      else (DOWNLOAD_TASK, UPLOAD_TASK, "Jianyu-Production-Sync-1110"))
        try:
            states = wait_for_tasks(task_names)
            print("计划任务协调完成：", states, flush=True)
        except Exception as error:
            wait_error = f"{type(error).__name__}: {error}"
            print("计划任务协调失败：", wait_error, flush=True)
    if mode == "download":
        path, summary = latest_summary(day, "*-summary.json",
                                       exclude=("salesforce-", "watchdog-"))
        if path:
            record_watchdog(mode, day, "ok", {"summary": str(path)})
            return 0
        subject = f"[剑鱼自动化][沙盒+生产][异常] {day} 10:50 未生成下载汇总"
        body = (f"{day} 10:50 看门狗未找到当天下载汇总。\n"
                "下载任务可能未启动、仍在运行或异常退出，请检查任务计划和浏览器日志。\n")
        if wait_error:
            body += f"任务协调错误：{wait_error}\n"
        message = enqueue_email(subject, body, kind="watchdog_download",
                                environment="沙盒+生产",
                                dedupe_key=f"download-watchdog:{day}")
        record_watchdog(mode, day, "alerted", {"mail_id": message.get("id")})
        return 1
    subject, body, success = combined_report(day)
    if wait_error:
        body += f"\n任务协调错误：{wait_error}\n"
    message = enqueue_email(subject, body, kind="daily_combined",
                            environment="沙盒+生产",
                            dedupe_key=f"daily-combined:{day}")
    record_watchdog(mode, day, "ok" if success else "alerted",
                    {"mail_id": message.get("id"), "mail_status": message.get("status")})
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("download", "combined"), required=True)
    parser.add_argument("--day")
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.mode, arguments.day))
