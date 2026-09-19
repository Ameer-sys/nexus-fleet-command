"""Simple priority/age ordering for pending warehouse jobs."""

from __future__ import annotations

from collections.abc import Iterable

from .jobs import Job, JobStatus


class JobScheduler:
    def pending(self, jobs: Iterable[Job]) -> list[Job]:
        """Return pending jobs by descending priority, then FIFO age."""

        pending = [job for job in jobs if job.status is JobStatus.PENDING]
        return sorted(
            pending,
            key=lambda job: (
                -job.priority,
                job.created_tick if job.created_tick is not None else 0,
                job.job_id,
            ),
        )
