"""The export query must keep the full tender group plus 中标、成交."""
import tempfile
import unittest
from pathlib import Path

from selenium.webdriver.common.by import By

from export_one_day import NOTICE_TYPES, NOTICE_TYPE_KEYS, OneDay, RecoverablePageError


class FilterJob(OneDay):
    def __init__(self, selected):
        self.driver = object()
        self.base = Path(tempfile.gettempdir())
        self.selected = set(selected)
        self.clicked = []

    def information_type_row(self):
        return self

    def selected_notice_types(self, row):
        return set(self.selected)

    def notice_type_option(self, row, name):
        return name

    def visible_click(self, option, wait_seconds=3):
        self.clicked.append(option)
        if option == '全部':
            self.selected = {'全部'}
        else:
            self.selected.discard('全部')
            self.selected.add(option)


class CheckedBox:
    def __init__(self, number):
        self.number = str(number)

    def get_attribute(self, name):
        return self.number if name == 'value' else None

    def is_selected(self):
        return True


class QueryDriver:
    def find_elements(self, by, locator):
        if by == By.CSS_SELECTOR and locator.startswith('input.el-checkbox__original'):
            return [CheckedBox(number) for number in range(1, 5)]
        return []


class QueryJob(OneDay):
    def __init__(self, type_error=False):
        self.driver = QueryDriver()
        self.day = '2025-01-27'
        self.events = []
        self.state = {}
        self.type_error = type_error

    def close_no_data(self):
        pass

    def filter_page(self):
        pass

    def dates(self):
        self.events.append('dates')

    def select_notice_types(self):
        self.events.append('types')
        if self.type_error:
            raise RuntimeError('类型未确认')

    def body(self):
        return ('关键词: 超融合 关键词: 分布式存储 关键词: 私有云 '
                '关键词: 虚拟化 例： 为您筛选到 3 条数据')

    def filter_submit(self):
        self.events.append('submit')

    def save(self, **changes):
        self.state.update(changes)


class RetrySwitch:
    def default_content(self):
        pass


class RetryDriver:
    def __init__(self):
        self.switch_to = RetrySwitch()
        self._jianyu_filter_handle = 'filter'
        self.urls = []

    def get(self, url):
        self.urls.append(url)


class DateRetryJob(OneDay):
    def __init__(self):
        self.driver = RetryDriver()
        self.attempts = 0

    def reauthenticate_if_needed(self):
        return False

    def _set_dates_once(self):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError('未找到唯一的一对日期输入框，需要核实日期控件。')
        return ['start', 'end']

    def find_page(self, predicate, timeout=25, window_filter=None):
        return True


class ScanDriver:
    def __init__(self, appear_after=None):
        self.appear_after = appear_after
        self.lookups = 0
        self.scrolls = []
        self.element = type('Element', (), {'is_displayed': lambda self: True})()

    def find_elements(self, by, locator):
        self.lookups += 1
        if self.appear_after is not None and self.lookups >= self.appear_after:
            return [self.element]
        return []

    def execute_script(self, script, ratio):
        self.scrolls.append(ratio)


class NoticeTypeFilterTest(unittest.TestCase):
    def test_unfiltered_page_selects_only_requested_types(self):
        job = FilterJob(set())
        job.select_notice_types()
        self.assertEqual(job.selected, set(NOTICE_TYPE_KEYS))
        self.assertEqual(job.clicked, list(NOTICE_TYPE_KEYS))

    def test_repeated_query_does_not_toggle_types_off(self):
        job = FilterJob(set(NOTICE_TYPE_KEYS))
        job.select_notice_types()
        self.assertEqual(job.clicked, [])

    def test_all_is_replaced_by_requested_group_and_results(self):
        job = FilterJob({'全部'})
        job.select_notice_types()
        self.assertEqual(job.clicked, list(NOTICE_TYPE_KEYS))
        self.assertEqual(job.selected, set(NOTICE_TYPE_KEYS))

    def test_stale_other_types_are_cleared_first(self):
        job = FilterJob({'招标信用信息_合同', '招标结果_成交'})
        job.select_notice_types()
        self.assertEqual(job.clicked, ['全部', *NOTICE_TYPE_KEYS])
        self.assertEqual(job.selected, set(NOTICE_TYPE_KEYS))

    def test_query_applies_types_before_submitting(self):
        job = QueryJob()
        count = job.query_filters(lambda _: job.events.append('regions'))
        self.assertEqual(count, 3)
        self.assertEqual(job.events, ['dates', 'types', 'regions', 'submit'])
        self.assertEqual(job.state['notice_types'], list(NOTICE_TYPES))

    def test_query_stops_if_type_selection_is_unverified(self):
        job = QueryJob(type_error=True)
        with self.assertRaisesRegex(RuntimeError, '类型未确认'):
            job.query_filters(lambda _: job.events.append('regions'))
        self.assertEqual(job.events, ['dates', 'types'])

    def test_transient_missing_date_pair_refreshes_and_retries(self):
        job = DateRetryJob()
        self.assertEqual(job.dates(), ['start', 'end'])
        self.assertEqual(job.attempts, 2)
        self.assertEqual(len(job.driver.urls), 1)

    def test_unique_scans_page_until_control_appears(self):
        job = OneDay.__new__(OneDay)
        job.driver = ScanDriver(appear_after=4)
        self.assertIs(job.unique(By.ID, 'target'), job.driver.element)
        self.assertEqual(job.driver.scrolls, [0, 0.25, 0.5])

    def test_unique_reports_recoverable_error_after_full_scan(self):
        job = OneDay.__new__(OneDay)
        job.driver = ScanDriver()
        with self.assertRaises(RecoverablePageError):
            job.unique(By.ID, 'target')
        self.assertEqual(job.driver.scrolls, [0, 0.25, 0.5, 0.75, 1])


if __name__ == '__main__':
    unittest.main()
