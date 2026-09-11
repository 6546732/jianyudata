import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from export_range import ExportRange, PROVINCES, province_key
from email_notice import SMTPNotifier


class Mail:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, record):
        if self.fail:
            raise ConnectionError('offline')
        self.sent.append(dict(record))


class Backend:
    def __init__(self, base, data, balance=800):
        self.base, self.data, self.quota = Path(base), data, balance
        self.submissions, self.recovered = [], []
        self.crash = False

    def balance(self):
        return self.quota

    def query(self, day, regions):
        data = self.data.get(day, {})
        return sum(data.get(r, 0) for r in regions) if regions else sum(data.values())

    def regions(self, day):
        return list(reversed(self.data[day]))

    def prepare(self, day, regions, count):
        self.prepared = (day, regions, count)
        return self.quota, count

    def submit(self, batch_id):
        self.submissions.append(self.prepared)
        self.quota -= self.prepared[2]
        file = self.base / f'{batch_id}.xlsx'
        file.write_bytes(b'backend-validated-file')
        if self.crash:
            raise ConnectionError('response lost after submit')
        return file

    def recover(self, pending):
        self.recovered.append(pending['batch_id'])
        return self.base / f"{pending['batch_id']}.xlsx"


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.day = date(2026, 9, 10)
        self.mail = Mail()

    def run_job(self, backend, end='2025-01-01', confirm=True):
        return ExportRange(backend, self.base, end=end, confirm=confirm,
                           today=lambda: self.day, notifier=self.mail).run()

    def test_whole_days_accumulate_then_resume(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 300}, '2025-01-02': {'北京': 500}})
        state = self.run_job(backend, end='2025-01-03')
        self.assertEqual([s[2] for s in backend.submissions], [300, 500])
        self.assertEqual(state['remaining'], 0)
        self.day = date(2026, 9, 11)
        backend.quota = 800
        state = self.run_job(backend, end='2025-01-03')
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(len(backend.submissions), 2)

    def test_province_prefix_order_and_next_day(self):
        backend = Backend(self.base, {'2025-01-01': {'北京': 400, '安徽': 300, '福建': 200}}, 500)
        state = self.run_job(backend)
        self.assertEqual(backend.submissions, [('2025-01-01', ['安徽'], 300)])
        self.assertEqual([i['region'] for i in state['queue']], ['北京', '福建'])
        self.assertEqual(state['remaining'], 200)
        self.day = date(2026, 9, 11)
        backend.quota = 800
        state = self.run_job(backend)
        self.assertEqual(backend.submissions[-1], ('2025-01-01', ['北京', '福建'], 600))
        self.assertEqual(state['status'], 'complete')

    def test_skip_over_800_and_notify_once(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 801, '北京': 800}})
        state = self.run_job(backend)
        self.assertEqual(state['skipped'][0]['region'], '安徽')
        self.assertEqual(backend.submissions[0][2], 800)
        self.assertEqual(len(self.mail.sent), 1)
        self.day = date(2026, 9, 11)
        backend.quota = 800
        state = self.run_job(backend)
        self.assertEqual(state['status'], 'complete_with_skips')
        self.assertEqual(len(self.mail.sent), 1)

    def test_mail_failure_does_not_block_and_retries(self):
        self.mail.fail = True
        backend = Backend(self.base, {'2025-01-01': {'安徽': 900, '北京': 100}})
        state = self.run_job(backend)
        self.assertEqual(state['status'], 'complete_with_skips')
        self.assertEqual(state['notification_failures'], 1)
        self.assertEqual(len(backend.submissions), 1)
        self.mail.fail = False
        state = self.run_job(backend)
        self.assertEqual(state['notification_failures'], 0)
        self.assertEqual(len(self.mail.sent), 1)

    def test_pending_recovers_without_resubmit(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 200}})
        backend.crash = True
        with self.assertRaises(ConnectionError):
            self.run_job(backend)
        backend.crash = False
        state = self.run_job(backend)
        self.assertEqual(len(backend.submissions), 1)
        self.assertEqual(len(backend.recovered), 1)
        self.assertEqual(state['remaining'], 600)

    def test_preview_neither_sends_nor_skips(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 801}})
        state = self.run_job(backend, confirm=False)
        self.assertEqual(state['status'], 'preview_skip')
        self.assertEqual(state['skipped'], [])
        self.assertEqual(self.mail.sent, [])
        self.assertEqual(backend.submissions, [])

    def test_preview_order_does_not_submit(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 10}})
        state = self.run_job(backend, confirm=False)
        self.assertEqual(state['status'], 'preview')
        self.assertEqual(backend.submissions, [])

    def test_actual_zero_quota(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 50}}, 0)
        self.assertEqual(self.run_job(backend)['status'], 'waiting_quota')
        self.assertEqual(backend.submissions, [])

    def test_balance_decreases_before_checkout_replans(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 200, '北京': 400}})
        original = backend.prepare
        def prepare(*args):
            backend.quota = 300
            return original(*args)
        backend.prepare = prepare
        state = self.run_job(backend)
        self.assertEqual(backend.submissions, [('2025-01-01', ['安徽'], 200)])
        self.assertEqual(state['remaining'], 100)

    def test_missing_province_fails_closed(self):
        backend = Backend(self.base, {'2025-01-01': {'安徽': 600, '北京': 400}})
        backend.regions = lambda _: ['安徽']
        with self.assertRaisesRegex(RuntimeError, '省份合计'):
            self.run_job(backend)
        self.assertEqual(backend.submissions, [])

    def test_lock_is_not_removed_by_second_job(self):
        lock = self.base / 'one_day.lock'
        lock.write_text('other worker')
        with self.assertRaises(FileExistsError):
            self.run_job(Backend(self.base, {}))
        self.assertTrue(lock.exists())

    def test_pinyin_order(self):
        self.assertLess(province_key('黑龙江省'), province_key('河南省'))
        self.assertLess(province_key('江西省'), province_key('吉林省'))
        self.assertLess(province_key('山东省'), province_key('上海市'))
        self.assertEqual(len(PROVINCES), 34)

    def test_smtp_content_and_recipient(self):
        mail = SMTPNotifier('smtp.example.com', 465, 'sender@example.com', 'zihao.zhang@smartx.com')
        with patch('email_notice.smtplib.SMTP_SSL') as smtp:
            client = smtp.return_value
            client.send_message.return_value = {}
            mail.send({'date': '2025-01-01', 'region': '安徽', 'count': 801})
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'zihao.zhang@smartx.com')
            self.assertIn('801', str(message['Subject']))
            self.assertIn('已跳过', str(message['Subject']))
            self.assertIn('安徽', message.get_content())


if __name__ == '__main__':
    unittest.main()
