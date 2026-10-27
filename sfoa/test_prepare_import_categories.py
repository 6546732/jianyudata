"""Only the requested announcement categories may enter the Salesforce CSV."""
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

import prepare_import
from prepare_import import ALLOWED_CATEGORIES, BLOCKED_FIELDS, FIELDS, MAX_CONTENT_CHARS, prepare


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

    def test_oversized_content_is_quarantined_without_blocking_valid_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = Workbook()
            sheet = workbook.active
            header = [''] * 33
            header[8] = '发布时间'
            sheet.append(header)
            sheet.append(['字段'] * 33)
            for number, content in ((1, '正常公告正文'), (2, '测试占位正文')):
                row = [''] * 33
                row[0] = str(number)
                row[2] = '北京'
                row[3] = '北京市'
                row[5] = f'公告{number}'
                row[6] = '招标'
                row[7] = content
                row[8] = '2025-01-01'
                row[11] = f'项目{number}'
                row[13] = f'编号{number}'
                sheet.append(row)
            source = root / 'export.xlsx'
            workbook.save(source)

            original_record = prepare_import.record

            def record_with_long_content(row, file_name, errors):
                item = original_record(row, file_name, errors)
                if item['Full_Title__c'] == '公告2':
                    content = '超长正文' * (MAX_CONTENT_CHARS // 4 + 1)
                    item['Source_Content__c'] = content
                    item['content__c'] = content[:32768]
                    item['Source_Content_Hash__c'] = hashlib.sha256(
                        content.encode('utf-8')).hexdigest()
                return item

            with patch('prepare_import.record', side_effect=record_with_long_content):
                report = prepare(root, root / 'upload', selected_files=[source])
            with (root / 'upload' / 'bidnews_upsert.csv').open(
                    newline='', encoding='utf-8') as stream:
                valid_rows = list(csv.DictReader(stream))
            with Path(report['blocked_rows_file']).open(
                    newline='', encoding='utf-8') as stream:
                csv.field_size_limit(MAX_CONTENT_CHARS * 2)
                blocked_rows = list(csv.DictReader(stream))

            self.assertEqual(report['rows_written'], 1)
            self.assertEqual(report['blocked_rows'], 1)
            self.assertEqual(len(valid_rows), 1)
            self.assertEqual(valid_rows[0]['Full_Title__c'], '公告1')
            self.assertEqual(len(blocked_rows), 1)
            self.assertEqual(list(blocked_rows[0])[-3:],
                             ['Source_File_Path', 'Content_Length', 'Block_Reason'])
            self.assertGreater(int(blocked_rows[0]['Content_Length']), MAX_CONTENT_CHARS)
            self.assertEqual(len(blocked_rows[0]['Source_Content__c']),
                             int(blocked_rows[0]['Content_Length']))
            self.assertEqual(len(BLOCKED_FIELDS), len(FIELDS) + 3)


if __name__ == '__main__':
    unittest.main()
