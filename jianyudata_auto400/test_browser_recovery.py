"""Safe recovery checks; these tests never launch or close a real browser."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import browser_session


class BrowserRecoveryTests(unittest.TestCase):
    def test_refuses_to_close_unverified_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = Mock(_jianyu_profile=str(Path(directory) / 'other'), _jianyu_port=9222)
            with self.assertRaisesRegex(RuntimeError, '拒绝关闭'):
                browser_session.restart_browser('chrome.exe', directory, driver)
            driver.execute_cdp_cmd.assert_not_called()

    def test_restarts_only_same_verified_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = (Path(directory) / 'notebook_chrome').resolve()
            driver = Mock(_jianyu_profile=str(profile), _jianyu_port=9222)
            replacement = Mock()
            with (patch.object(browser_session, 'endpoint', side_effect=[True, False, False]),
                  patch.object(browser_session, 'chrome_commands', return_value=[]),
                  patch.object(browser_session, 'profile_ports', return_value=([], False)),
                  patch.object(browser_session, 'connect_browser', return_value=replacement) as connect,
                  patch.object(browser_session.time, 'sleep')):
                self.assertIs(browser_session.restart_browser('chrome.exe', directory, driver), replacement)
            driver.execute_cdp_cmd.assert_called_once_with('Browser.close', {})
            connect.assert_called_once_with('chrome.exe', directory, preferred_profile=profile)

    def test_starts_verified_profile_again_after_browser_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = (Path(directory) / 'notebook_chrome_v7').resolve()
            driver = Mock(_jianyu_profile=str(profile), _jianyu_port=7003)
            replacement = Mock()
            with (patch.object(browser_session, 'endpoint', return_value=False),
                  patch.object(browser_session, 'chrome_commands', return_value=[]),
                  patch.object(browser_session, 'profile_ports', return_value=([], False)),
                  patch.object(browser_session, 'connect_browser', return_value=replacement) as connect):
                self.assertIs(browser_session.restart_browser('chrome.exe', directory, driver), replacement)
            driver.execute_cdp_cmd.assert_not_called()
            connect.assert_called_once_with('chrome.exe', directory, preferred_profile=profile)

    def test_does_not_force_close_dead_port_with_occupied_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = (Path(directory) / 'notebook_chrome').resolve()
            driver = Mock(_jianyu_profile=str(profile), _jianyu_port=9222)
            with (patch.object(browser_session, 'endpoint', return_value=False),
                  patch.object(browser_session, 'chrome_commands', return_value=['chrome command']),
                  patch.object(browser_session, 'profile_ports', return_value=([9222], True))):
                with self.assertRaisesRegex(RuntimeError, '仍被占用'):
                    browser_session.restart_browser('chrome.exe', directory, driver)
            driver.execute_cdp_cmd.assert_not_called()

    def test_no_blank_point_means_no_click(self):
        driver = Mock()
        driver.execute_script.return_value = None
        self.assertEqual(browser_session.click_safe_blank(driver),
                         {'clicked': False, 'reason': '没有可确认的安全空白处'})

    def test_blank_point_changed_before_click_is_not_clicked(self):
        driver = Mock()
        driver.execute_script.side_effect = [
            {'element': Mock(), 'x': 24, 'y': 500, 'dx': 0, 'dy': 0, 'target': 'body#.'},
            False,
        ]
        with patch('selenium.webdriver.common.action_chains.ActionChains') as actions:
            result = browser_session.click_safe_blank(driver)
        self.assertFalse(result['clicked'])
        self.assertIn('已变化', result['reason'])
        actions.return_value.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
