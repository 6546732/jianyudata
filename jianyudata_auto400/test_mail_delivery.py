import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mail_delivery as mail


class MailDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.queue = Path(self.temporary.name)
        self.clock = datetime(2026, 10, 10, 11, 20,
                              tzinfo=timezone(timedelta(hours=8)))

    def tearDown(self):
        self.temporary.cleanup()

    def test_failure_retries_after_30_minutes_and_stops_at_five(self):
        with unittest.mock.patch.object(mail, "now", return_value=self.clock):
            record = mail.enqueue_email("subject", "body", dedupe_key="one",
                                        immediate=False, queue_dir=self.queue)
        path = self.queue / f"{record['id']}.json"

        def fail(_):
            raise RuntimeError("network")

        for attempt in range(5):
            current = self.clock + timedelta(minutes=30 * attempt)
            record = mail.attempt_file(path, current=current, sender=fail)
            self.assertEqual(record["attempts"], attempt + 1)
            if attempt < 4:
                self.assertEqual(record["status"], "pending")
                self.assertEqual(datetime.fromisoformat(record["next_attempt_at"]),
                                 current + timedelta(minutes=30))
        self.assertEqual(record["status"], "permanently_failed")

    def test_duplicate_key_creates_one_message(self):
        with unittest.mock.patch.object(mail, "now", return_value=self.clock):
            first = mail.enqueue_email("a", "b", dedupe_key="daily", immediate=False,
                                       queue_dir=self.queue)
            second = mail.enqueue_email("changed", "changed", dedupe_key="daily",
                                        immediate=False, queue_dir=self.queue)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list(self.queue.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
