"""Only the requested announcement categories may enter the Salesforce CSV."""
import csv
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from prepare_import import ALLOWED_CATEGORIES, prepare


class ImportCategoryTest(unittest.TestCase):
    def test_prepare_skips_unrequested_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = Workbook()
            sheet = workbook.active
            header = [''] * 33
            header[8] = '发布时间'
            sheet.append(header)
            sheet.append(['字段'] * 33)
            for number, category in enumerate(
                    ['招标', '邀标', '询价', '竞谈', '单一', '竞价', '变更',
                     '中标', '成交', '合同'], 1):
                row = [''] * 33
                row[0] = str(number)
                row[2] = '北京'
                row[3] = '北京市'
                row[5] = f'公告{number}'
                row[6] = category
                row[7] = f'公告正文{number}'
                row[8] = '2025-01-01'
                row[11] = f'项目{number}'
                row[13] = f'编号{number}'
                sheet.append(row)
            source = root / 'export.xlsx'
            workbook.save(source)
            report = prepare(root, root / 'upload', selected_files=[source])
            with (root / 'upload' / 'bidnews_upsert.csv').open(
                    newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(report['rows_read'], 10)
            self.assertEqual(report['rows_written'], 9)
            self.assertEqual(report['categories_skipped'], {'合同': 1})
            self.assertEqual({row['Publication_Category__c'] for row in rows},
                             ALLOWED_CATEGORIES)
            self.assertEqual({row['Tender_Stage__c'] for row in rows
                              if row['Publication_Category__c'] in {'中标', '成交'}},
                             {'已成交'})
            self.assertEqual({row['Tender_Stage__c'] for row in rows
                              if row['Publication_Category__c'] not in {'中标', '成交'}},
                             {'招标中'})


if __name__ == '__main__':
    unittest.main()
