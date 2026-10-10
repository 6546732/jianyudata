import unittest

from export_one_day import RecoverablePageError
from fast_range import FastRange


class _Backend:
    def regions(self, _day):
        return ['北京', '上海']


def _job(counts):
    job = FastRange.__new__(FastRange)
    job.backend = _Backend()
    job.state = {'current_date': '2025-05-22'}
    values = iter(counts)
    job.count = lambda _regions: next(values)
    job.save = lambda: None
    return job


class FastRangeCoverageToleranceTest(unittest.TestCase):
    def test_national_only_difference_within_twenty_continues(self):
        job = _job([193, 192, 193])

        job.initialize_queue(182)

        self.assertEqual(193, job.state['national_count'])
        self.assertEqual(1, job.state['omitted_national'][0]['count'])
        self.assertEqual(['北京', '上海'], [item['region'] for item in job.state['queue']])

    def test_difference_over_twenty_stops_before_submission(self):
        job = _job([193, 172, 193])

        with self.assertRaises(RecoverablePageError):
            job.initialize_queue(182)


if __name__ == '__main__':
    unittest.main()
