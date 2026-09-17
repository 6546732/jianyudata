"""Read-only nationwide daily result counts from Jianyu; never open an order.

Run once to scan from 2025-01-01 through today's China date. Successful days
are checkpointed, so running the same command again only fills missing days.
"""
import argparse
import json
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from selenium.webdriver.common.by import By

from automatic_ui import AutomaticUI
from browser_session import connect_browser
from export_one_day import OneDay, WORDS

CHINA_TIME = timezone(timedelta(hours=8))
DEFAULT_BASE = Path(r'D:\桌面\data')
CRITERIA = {
    'region': '全国',
    'keywords': sorted(WORDS),
    'match_methods': ['标题', '全文', '附件', '项目名称/标的物'],
}


def scan_days(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('开始日期不能晚于结束日期')
    if last > datetime.now(CHINA_TIME).date():
        raise ValueError('结束日期不能晚于今天（北京时间）')
    while first <= last:
        yield first.isoformat()
        first += timedelta(days=1)


def load_checkpoint(path):
    records = {}
    if not path.exists():
        return records
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        entry = json.loads(line)
        day = date.fromisoformat(entry['date']).isoformat()
        count = entry['national_count']
        if entry.get('criteria') != CRITERIA or type(count) is not int or count < 0:
            raise RuntimeError(f'第{number}行条件或条数无效，请检查 {path}')
        if day in records:
            raise RuntimeError(f'第{number}行日期重复：{day}；请先检查 {path}')
        records[day] = entry
    return records


def save_workbook(records, output):
    """Rebuild the small two-column Excel file atomically from checkpoint data."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '全国每日条数'
    sheet.append(['日期', '全国条数'])
    for day in sorted(records):
        sheet.append([date.fromisoformat(day), records[day]['national_count']])
        sheet.cell(sheet.max_row, 1).number_format = 'yyyy-mm-dd'
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='1C788D')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(horizontal='center')
    sheet.column_dimensions['A'].width = 17
    sheet.column_dimensions['B'].width = 16
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = f'A1:B{sheet.max_row}'
    temporary = output.with_name(output.stem + '.tmp.xlsx')
    workbook.save(temporary)
    os.replace(temporary, output)


def append_checkpoint(path, day, count):
    entry = {
        'date': day, 'national_count': count,
        'queried_at': datetime.now(CHINA_TIME).isoformat(),
        'criteria': CRITERIA,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def collect(start, end, checkpoint, output, query):
    days = list(scan_days(start, end))
    records = load_checkpoint(checkpoint)
    if output.exists() and not checkpoint.exists():
        raise RuntimeError('Excel已存在但进度文件不存在；为防止覆盖，请先核对文件。')
    done = 0
    for day in days:
        if day in records:
            continue
        count = query(day)
        if type(count) is not int or count < 0:
            raise RuntimeError(f'{day} 查询条数无效，未记录：{count!r}')
        records[day] = append_checkpoint(checkpoint, day, count)
        save_workbook(records, output)
        done += 1
        print(f'{day} 全国 {count} 条；已保存 {output}', flush=True)
    # A crash can occur after the checkpoint append but before the XLSX replace.
    # Always rebuild once at the end so rerunning repairs an incomplete workbook.
    save_workbook(records, output)
    print(f'完成：本次新增{done}天，表格共有{sum(day in records for day in days)}天。', flush=True)
    return records


def scheduled_export_running():
    command = (
        "(Get-ScheduledTask -TaskName 'Jianyu-Notebook-2100' "
        "-ErrorAction Stop).State.ToString()"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, timeout=25,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode:
        raise RuntimeError('无法确认自动导出任务是否正在运行，停止以避免共用浏览器。')
    return result.stdout.strip().lower() == 'running'


class BrowserCounter:
    def __init__(self, base):
        self.base = base
        self.driver = None
        self.ui = AutomaticUI()
        self.last_task_check = 0.0

    def connect(self):
        if scheduled_export_running():
            raise RuntimeError('每日自动导出正在运行，请等待它结束后再统计。')
        self.last_task_check = time.monotonic()
        candidates = [Path(os.environ.get(key, '')) / 'Google/Chrome/Application/chrome.exe'
                      for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
        chrome = next((item for item in candidates if item.is_file()), None)
        if chrome is None:
            raise RuntimeError('未找到 Google Chrome。')
        self.driver = connect_browser(chrome, self.base)
        # 只在现有登录失效时才读取本机加密保存的密码；不在脚本中保存密码。
        from auto_login import PasswordLogin
        from nightly_job import load_login
        username, password = load_login()
        login = PasswordLogin(username, password, self.base)
        login.ensure(self.driver)
        OneDay.reauthenticate = login.ensure

    def __call__(self, day):
        if self.driver is None:
            self.connect()
        if time.monotonic() - self.last_task_check > 60:
            if scheduled_export_running():
                raise RuntimeError('每日自动导出已启动；统计暂停。重新运行此命令即可续查。')
            self.last_task_check = time.monotonic()
        job = OneDay(self.driver, day, self.base / 'national_count_state', False)
        job.state_path = self.base / 'national_count_state' / 'latest_query.json'
        count = job.query_filters(lambda current: self.ui.select_regions(current, []),
                                  self.ui.wait_query)
        chips = {e.text.strip() for e in self.driver.find_elements(
            By.CSS_SELECTOR, '#area-del .delete-close') if e.is_displayed()}
        if chips != {'全国'}:
            raise RuntimeError(f'{day} 地区不是唯一的“全国”：{chips}；未记录条数。')
        if any(date.fromisoformat(day) != date(*map(int, re.findall(r'\d+', e.get_attribute('value'))))
               for e in job.date_inputs()):
            raise RuntimeError(f'{day} 日期显示不一致；未记录条数。')
        return count


def main():
    parser = argparse.ArgumentParser(description='逐天统计全国匹配条数，不创建订单或消耗额度')
    parser.add_argument('--start', default='2025-01-01', help='起始日期，默认2025-01-01')
    parser.add_argument('--end', default=datetime.now(CHINA_TIME).date().isoformat(),
                        help='结束日期，默认运行当天（北京时间）')
    parser.add_argument('--base', type=Path, default=DEFAULT_BASE)
    args = parser.parse_args()
    base = args.base.resolve()
    checkpoint = base / 'national_daily_counts.jsonl'
    output = base / 'national_daily_counts.xlsx'
    counter = BrowserCounter(base)
    base.mkdir(parents=True, exist_ok=True)
    lock_path = base / 'national_daily_counts.lock'
    with lock_path.open('a+b') as lock:
        import msvcrt
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('全国条数统计已在运行；不要同时启动第二份。') from error
        try:
            collect(args.start, args.end, checkpoint, output, counter)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    main()
