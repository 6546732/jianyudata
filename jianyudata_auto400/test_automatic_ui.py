"""Regression checks for national-region selection across repeated daily queries."""

import unittest

from selenium.webdriver.common.by import By

from automatic_ui import AutomaticUI


class Chip:
    def __init__(self, name):
        self.text = name

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class Driver:
    def __init__(self, selected):
        self.selected = set(selected)
        self.clicks = 0

    def find_elements(self, by, locator):
        if by == By.CSS_SELECTOR and locator == '#area-del .delete-close':
            return [Chip(name) for name in self.selected]
        return []

    def execute_script(self, script, *args):
        if 'elementFromPoint' in script:
            return True
        if 'arguments[0].click()' in script:
            self.clicks += 1
            if args[0].text == '全国':
                self.selected = set() if self.selected == {'全国'} else {'全国'}
            else:
                self.selected.discard('全国')
                self.selected.add(args[0].text)
        return None


class Job:
    def __init__(self, selected):
        self.driver = Driver(selected)

    def unique(self, by, locator):
        import re
        name = re.search(r"normalize-space\(\.\)='([^']+)'", locator)
        return Chip(name.group(1) if name else '全国')

    def visible_click(self, element, wait_seconds=3):
        raise AssertionError('无遮挡的地区控件应使用页面事件')


class NationalSelectionTest(unittest.TestCase):
    def setUp(self):
        self.ui = AutomaticUI()
        self.ui.arm_query = lambda job: None

    def test_repeated_national_query_does_not_toggle_selection_off(self):
        job = Job({'全国'})
        self.ui.select_regions(job, [])
        self.ui.select_regions(job, [])
        self.assertEqual(getattr(job.driver, 'clicks', 0), 0)
        self.assertEqual(job.driver.selected, {'全国'})

    def test_empty_chips_are_already_national(self):
        job = Job(set())
        self.ui.select_regions(job, [])
        self.assertEqual(getattr(job.driver, 'clicks', 0), 0)

    def test_province_selection_switches_to_national_once(self):
        job = Job({'北京'})
        self.ui.select_regions(job, [])
        self.assertEqual(job.driver.clicks, 1)
        self.assertEqual(job.driver.selected, {'全国'})

    def test_national_to_province_selects_province_directly(self):
        job = Job({'全国'})
        self.ui.select_regions(job, ['安徽'])
        self.assertEqual(job.driver.clicks, 1)
        self.assertEqual(job.driver.selected, {'安徽'})


if __name__ == '__main__':
    unittest.main()
