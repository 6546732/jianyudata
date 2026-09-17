"""Offline tests: no Selenium session, site request, order or quota use."""
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from national_daily_counts import collect, load_checkpoint


class NationalCountsTests(unittest.TestCase):
    def test_inclusive_scan_zero_count_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            checkpoint, output = base / 'counts.jsonl', base / 'counts.xlsx'
            called = []

            def query(day):
                called.append(day)
                return {'2025-01-01': 16, '2025-01-02': 0}[day]

            collect('2025-01-01', '2025-01-02', checkpoint, output, query)
            self.assertEqual(called, ['2025-01-01', '2025-01-02'])
            self.assertEqual(len(load_checkpoint(checkpoint)), 2)

            # Simulate a crash after checkpoint append but before XLSX replace.
            output.unlink()
            collect('2025-01-01', '2025-01-02', checkpoint, output, query)
            self.assertEqual(called, ['2025-01-01', '2025-01-02'])
            workbook = load_workbook(output, read_only=True, data_only=True)
            rows = list(workbook.active.values)
            self.assertEqual(rows[0], ('日期', '全国条数'))
            self.assertEqual([row[1] for row in rows[1:]], [16, 0])
            self.assertEqual([row[0].date().isoformat() for row in rows[1:]],
                             ['2025-01-01', '2025-01-02'])
            workbook.close()

    def test_invalid_result_does_not_enter_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            checkpoint, output = base / 'counts.jsonl', base / 'counts.xlsx'
            with self.assertRaisesRegex(RuntimeError, '条数无效'):
                collect('2025-01-01', '2025-01-01', checkpoint, output,
                        lambda _: -1)
            self.assertFalse(checkpoint.exists())
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
