"""One scheduled filter-only smoke test. Never creates an export order."""
import json
from datetime import datetime
from pathlib import Path

from selenium.webdriver.common.by import By

from auto_login import PasswordLogin
from automatic_ui import AutomaticUI
from browser_session import connect_browser
from export_one_day import NOTICE_TYPE_KEYS, NOTICE_TYPES, OneDay
from nightly_job import load_login


BASE = Path(r'D:\桌面\data')
DAY = '2025-01-27'
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
DRIVER = Path(
    r'C:\Users\Administrator\.cache\selenium\chromedriver\win64'
    r'\152.0.7977.82\chromedriver.exe'
)


def run(day=DAY):
    if not CHROME.is_file() or not DRIVER.is_file():
        raise RuntimeError('Chrome 或已验证的 ChromeDriver 不存在；只查询测试停止。')
    driver = connect_browser(CHROME, BASE, chromedriver_path=DRIVER)
    username, password = load_login()
    login = PasswordLogin(username, password, BASE)
    login.ensure(driver)
    OneDay.reauthenticate = login.ensure
    del password

    job = OneDay(driver, day, BASE / 'read_only_notice_type_test', confirm=False)
    ui = AutomaticUI()
    count = job.query_filters(lambda current: ui.select_regions(current, []), ui.wait_query)
    selected = job.selected_notice_types(job.information_type_row())
    regions = {element.text.strip() for element in driver.find_elements(
        By.CSS_SELECTOR, '#area-del .delete-close') if element.is_displayed()}
    if selected != set(NOTICE_TYPE_KEYS) or regions not in ({'全国'}, set()):
        raise RuntimeError(f'查询条件校验失败：类型={sorted(selected)}；地区={sorted(regions)}')
    if '/toCreateOrderPage/' in driver.execute_script('return location.href'):
        raise RuntimeError('意外进入订单页面；没有执行确认扣除。')

    result = {
        'time': datetime.now().astimezone().isoformat(),
        'date': day,
        'region': '全国',
        'keywords': ['超融合', '分布式存储', '私有云', '虚拟化'],
        'notice_types': list(NOTICE_TYPES),
        'selected_site_keys': list(NOTICE_TYPE_KEYS),
        'count': count,
        'status': 'filter_only_no_export',
    }
    log_dir = BASE / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f'notice_filter_only_{datetime.now():%Y%m%d}.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'只查询完成：{day}，全国，{count} 条。结果：{path}', flush=True)
    print('没有创建订单、下载文件、扣减额度或上传 Salesforce。', flush=True)
    return result


if __name__ == '__main__':
    run()
