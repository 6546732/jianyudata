"""Notebook 内持续调度；北京时间次日03:00续跑，不创建订单重试循环。"""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHINA = timezone(timedelta(hours=8))


def china_now():
    return datetime.now(CHINA)


def next_run(now):
    return (now.astimezone(CHINA) + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)


def run_daily(export, get_driver, before_run, base, start='2025-01-03', end=None,
              daily_limit=400, now=china_now, sleep=time.sleep, first_run_at=None):
    # 防止长时间等待期间再开启第二个调度器；与批次运行锁分离。
    lock = Path(base) / 'daily_scheduler.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open('x', encoding='utf-8') as f:
            f.write('自动调度中；仅在确认旧内核已停止后清除此锁')
    except FileExistsError:
        raise RuntimeError('已有自动调度器运行或遗留锁，请先确认旧Notebook已停止。')
    try:
        due = first_run_at if first_run_at is not None else next_run(now())
        if due.tzinfo is None:
            raise ValueError('首次运行时间必须带时区')
        print('已就绪；首次自动运行：', due.astimezone(CHINA).isoformat(), flush=True)
        while now() < due:
            sleep(min(30, max(0.1, (due - now()).total_seconds())))
        while True:
            driver = get_driver()
            before_run(driver)
            print(f'[{now().isoformat()}] 开始续跑，每日最多{daily_limit}条', flush=True)
            state = export(driver, base=base, start=start, end=end,
                           confirm=True, daily_limit=daily_limit, strategy='prefix')
            status = state['status']
            print('本轮状态：', status, '日期：', state['current_date'], '剩余预算：', state['remaining'], flush=True)
            if status not in {'waiting_quota', 'province_batch_done', 'day_changed', 'complete', 'complete_with_skips'}:
                print('需要处理后才能继续，调度停止：', status, flush=True)
                return state
            if end is not None and status in {'complete', 'complete_with_skips'}:
                return state
            # 无固定截止日期时，完成当前范围后明天继续处理新增日期。
            due = next_run(now())
            if status == 'day_changed':
                due = now().astimezone(CHINA).replace(hour=3, minute=0, second=0, microsecond=0)
                if due <= now():
                    continue
            print('保持本单元格运行；下次自动运行：', due.isoformat(), flush=True)
            while now() < due:
                sleep(min(30, max(0.1, (due - now()).total_seconds())))
    finally:
        lock.unlink(missing_ok=True)
