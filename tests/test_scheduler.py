"""Priority scheduler ordering tests."""

import unittest

from nexus import Job, JobScheduler


def make_job(job_id: str, priority: int, created_tick: int) -> Job:
    return Job(
        job_id,
        f"PACKAGE-{job_id}",
        pickup=(0.0, 0.0),
        dropoff=(1.0, 1.0),
        priority=priority,
        created_tick=created_tick,
    )


class SchedulerTests(unittest.TestCase):
    def test_higher_priority_jobs_are_selected_first(self) -> None:
        jobs = [
            make_job("LOW", 3, 0),
            make_job("CRITICAL", 10, 2),
            make_job("MEDIUM", 6, 1),
        ]
        ordered = JobScheduler().pending(jobs)
        self.assertEqual([job.job_id for job in ordered], ["CRITICAL", "MEDIUM", "LOW"])

    def test_equal_priority_uses_oldest_then_job_id(self) -> None:
        jobs = [
            make_job("NEW", 5, 7),
            make_job("B", 5, 2),
            make_job("A", 5, 2),
        ]
        ordered = JobScheduler().pending(jobs)
        self.assertEqual([job.job_id for job in ordered], ["A", "B", "NEW"])


if __name__ == "__main__":
    unittest.main()
