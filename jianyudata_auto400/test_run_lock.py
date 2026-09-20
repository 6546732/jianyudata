"""Windows 内核锁必须允许恢复陈旧文件，同时阻止真实并发。"""
import tempfile
import unittest
from pathlib import Path

from export_range import acquire_run_lock, release_run_lock


class RunLockTest(unittest.TestCase):
    def test_stale_lock_file_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'one_day.lock'
            path.write_text('旧任务留下的文件', encoding='utf-8')
            handle = acquire_run_lock(path)
            release_run_lock(handle, path)
            self.assertFalse(path.exists())

    def test_real_concurrent_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'one_day.lock'
            first = acquire_run_lock(path)
            try:
                with self.assertRaisesRegex(RuntimeError, '真实运行中'):
                    acquire_run_lock(path)
            finally:
                release_run_lock(first, path)


if __name__ == '__main__':
    unittest.main()
