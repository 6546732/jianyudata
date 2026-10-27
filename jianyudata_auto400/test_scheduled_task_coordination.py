import unittest

from scheduled_task_coordination import wait_for_tasks


class ScheduledTaskCoordinationTest(unittest.TestCase):
    def test_waits_for_running_task(self):
        states = iter(("Running", "Running", "Ready"))
        sleeps = []
        result = wait_for_tasks(
            ("download",), timeout=10, startup_grace=0, poll=1,
            state_reader=lambda _: next(states), sleeper=sleeps.append,
            clock=lambda: len(sleeps),
        )
        self.assertEqual({"download": "Ready"}, result)
        self.assertEqual([1, 1], sleeps)

    def test_rechecks_task_that_starts_in_same_backfill_wave(self):
        states = iter(("Ready", "Running", "Ready"))
        sleeps = []
        result = wait_for_tasks(
            ("download",), timeout=10, startup_grace=2, poll=1,
            state_reader=lambda _: next(states), sleeper=sleeps.append,
            clock=lambda: sum(sleeps),
        )
        self.assertEqual({"download": "Ready"}, result)
        self.assertEqual([2, 1], sleeps)


if __name__ == "__main__":
    unittest.main()
