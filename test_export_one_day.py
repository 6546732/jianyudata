import unittest
import tempfile
from pathlib import Path
from zipfile import ZipFile
from export_one_day import order_counts, xlsx_rows


class Checks(unittest.TestCase):
    def test_quota(self):
        text = '本次扣除：16条 今日限量余额：776条 本日仍可导出：760条 购买须知：最低起售100元'
        self.assertEqual(order_counts(text), [16, 776, 760])

    def test_cash_blocks(self):
        quota = ' 本次扣除：16条 今日限量余额：776条 本日仍可导出：760条'
        for fee in ('实付金额：¥100', '实付金额：未知', '应付金额：0.01元', '实付金额：0,100'):
            with self.assertRaises(RuntimeError):
                order_counts(fee + quota)
        self.assertEqual(order_counts('实付金额：¥0.00' + quota)[0], 16)

    def test_over_quota(self):
        for text in ('本次扣除：801条 今日限量余额：900条 本日仍可导出：99条',
                     '本次扣除：16条 今日限量余额：10条 本日仍可导出：0条',
                     '本次扣除：16条 今日限量余额：776条 本日仍可导出：759条'):
            with self.assertRaises(RuntimeError): order_counts(text)

    def test_xlsx(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.xlsx'
            with ZipFile(path, 'w') as z:
                z.writestr('[Content_Types].xml', '<Types/>')
                z.writestr('xl/workbook.xml', '<workbook/>')
                z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c><v>1</v></c></row><row><c><v>2</v></c></row><row><c/></row></sheetData></worksheet>')
            self.assertEqual(xlsx_rows(path), 2)

if __name__ == '__main__': unittest.main()
