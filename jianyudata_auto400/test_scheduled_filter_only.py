"""Tomorrow's scheduled run must call only the filter-only probe."""
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import run_scheduled_notebook


class TomorrowClock:
    @staticmethod
    def now():
        return datetime(2026, 9, 19, 12, 5)


class MissedClock:
    @staticmethod
    def now():
        return datetime(2026, 9, 20, 12, 5)


class ScheduledFilterOnlyTest(unittest.TestCase):
    def test_tomorrow_runs_query_without_loading_export_notebook(self):
        probe = types.ModuleType('read_only_notice_test')
        probe.run = Mock(return_value={'status': 'filter_only_no_export'})
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pending.json'
            marker.write_text('{}', encoding='utf-8')
            with (patch.object(run_scheduled_notebook, 'LOGS', Path(directory)),
                  patch.object(run_scheduled_notebook, 'READ_ONLY_TEST_PENDING', marker),
                  patch.object(run_scheduled_notebook, 'datetime', TomorrowClock),
                  patch.dict(sys.modules, {'read_only_notice_test': probe})):
                result = run_scheduled_notebook.main()
            self.assertEqual(result, 0)
            probe.run.assert_called_once_with()
            log = next(Path(directory).glob('*.log')).read_text(encoding='utf-8')
            self.assertIn('只查询不导出', log)
            self.assertNotIn('自动执行Notebook', log)
            self.assertFalse(marker.exists())

    def test_missed_run_does_query_first(self):
        probe = types.ModuleType('read_only_notice_test')
        probe.run = Mock(return_value={'status': 'filter_only_no_export'})
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pending.json'
            marker.write_text('{}', encoding='utf-8')
            with (patch.object(run_scheduled_notebook, 'LOGS', Path(directory)),
                  patch.object(run_scheduled_notebook, 'READ_ONLY_TEST_PENDING', marker),
                  patch.object(run_scheduled_notebook, 'datetime', MissedClock),
                  patch.dict(sys.modules, {'read_only_notice_test': probe})):
                result = run_scheduled_notebook.main()
            self.assertEqual(result, 0)
            probe.run.assert_called_once_with()
            self.assertFalse(marker.exists())

    def test_failed_query_keeps_pending_marker(self):
        probe = types.ModuleType('read_only_notice_test')
        probe.run = Mock(side_effect=RuntimeError('filter unavailable'))
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pending.json'
            marker.write_text('{}', encoding='utf-8')
            with (patch.object(run_scheduled_notebook, 'LOGS', Path(directory)),
                  patch.object(run_scheduled_notebook, 'READ_ONLY_TEST_PENDING', marker),
                  patch.object(run_scheduled_notebook, 'datetime', TomorrowClock),
                  patch.object(run_scheduled_notebook, 'alert'),
                  patch.dict(sys.modules, {'read_only_notice_test': probe})):
                result = run_scheduled_notebook.main()
            self.assertEqual(result, 1)
            self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
