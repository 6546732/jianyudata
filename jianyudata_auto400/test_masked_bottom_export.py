"""Regression checks for using the fixed bottom export button through a stale mask."""
import tempfile
import unittest
from pathlib import Path

from selenium.webdriver.common.by import By

from export_one_day import OneDay


class Element:
    def __init__(self, text='', displayed=True, enabled=True):
        self.text = text
        self.displayed = displayed
        self.enabled = enabled
        self.clicked = 0
        self.children = {}

    def is_displayed(self):
        return self.displayed

    def is_enabled(self):
        return self.enabled

    def find_elements(self, by, selector):
        return self.children.get((by, selector), [])

    def click(self):
        self.clicked += 1


class Driver:
    def __init__(self, mask, footer, query=None, uncovered=True):
        self.mask = mask
        self.footer = footer
        self.query = query or {'pending': 0}
        self.uncovered = uncovered

    def find_elements(self, by, selector):
        if (by, selector) == (By.CSS_SELECTOR, '.loading_, .el-loading-mask'):
            return [self.mask]
        if (by, selector) == (By.CSS_SELECTOR, '.data-footer-main'):
            return [self.footer]
        return []

    def execute_script(self, script, *args):
        if '__autoQuery' in script:
            return self.query
        if 'elementFromPoint' in script:
            return self.uncovered
        return None


class MaskedBottomExportTest(unittest.TestCase):
    def make_job(self, mask_visible=True, uncovered=True):
        count = Element('11')
        button = Element('立即导出')
        footer = Element()
        footer.children[(By.CSS_SELECTOR, '.dataExNum')] = [count]
        footer.children[(By.CSS_SELECTOR, '.data-now-export.dataBtnCom')] = [button]
        driver = Driver(Element(displayed=mask_visible), footer, uncovered=uncovered)
        with tempfile.TemporaryDirectory() as directory:
            job = OneDay(driver, '2025-05-16', Path(directory), False)
        job.state = {'count': 11}
        job.scroll_to_stable_bottom = lambda: {
            'top': 3552, 'viewport': 587, 'height': 4138, 'remaining': -1}
        return job, button

    def test_clicks_unique_uncovered_bottom_button_when_mask_is_visible(self):
        job, button = self.make_job()
        self.assertTrue(job.export_through_visible_bottom_bar())
        self.assertEqual(button.clicked, 1)

    def test_does_nothing_without_visible_mask(self):
        job, button = self.make_job(mask_visible=False)
        self.assertFalse(job.export_through_visible_bottom_bar())
        self.assertEqual(button.clicked, 0)

    def test_does_not_click_button_still_covered_by_mask(self):
        job, button = self.make_job(uncovered=False)
        self.assertFalse(job.export_through_visible_bottom_bar())
        self.assertEqual(button.clicked, 0)


if __name__ == '__main__':
    unittest.main()
