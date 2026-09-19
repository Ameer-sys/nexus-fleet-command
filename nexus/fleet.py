"""Fleet orchestration for independent NEXUS robot simulations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from .auction import AuctionResult, Auctioneer
from .jobs import Job, JobStatus
from .robot import NexusRobot
from .scheduler import JobScheduler
from .traffic import TrafficManager


class Fleet:
    """Own a group of robots and advance each simulation one tick at a time."""

    def __init__(
        self,
        robots: Iterable[NexusRobot] = (),
        *,
        auctioneer: Auctioneer | None = None,
        scheduler: JobScheduler | None = None,
        traffic_manager: TrafficManager | None = None,
        traffic_enabled: bool = True,
    ) -> None:
        self.robots: dict[str, NexusRobot] = {}
        self.jobs: dict[str, Job] = {}
        self.auctioneer = auctioneer or Auctioneer()
        self.scheduler = scheduler or JobScheduler()
        self.traffic = traffic_manager or TrafficManager()
        self.traffic_enabled = traffic_enabled
        self.auction_history: list[AuctionResult] = []
        self._events: list[dict[str, Any]] = []
        self.steps = 0
        for robot in robots:
            self.add(robot)

    def add(self, robot: NexusRobot) -> None:
        if robot.id in self.robots:
            raise ValueError(f"duplicate robot id: {robot.id}")
        self.robots[robot.id] = robot

    def __getitem__(self, robot_id: str) -> NexusRobot:
        return self.robots[robot_id]

    def __iter__(self) -> Iterator[NexusRobot]:
        return iter(self.robots.values())

    @property
    def pending_jobs(self) -> list[Job]:
        return self.scheduler.pending(self.jobs.values())

    @property
    def completed_jobs(self) -> list[Job]:
        return [job for job in self.jobs.values() if job.status is JobStatus.COMPLETED]

    @property
    def failed_jobs(self) -> list[Job]:
        return [job for job in self.jobs.values() if job.status is JobStatus.FAILED]

    @property
    def all_jobs_finished(self) -> bool:
        return bool(self.jobs) and all(
            job.status in {JobStatus.COMPLETED, JobStatus.FAILED}
            for job in self.jobs.values()
        )

    def submit_job(self, job: Job) -> None:
        if job.job_id in self.jobs:
            raise ValueError(f"duplicate job id: {job.job_id}")
        if job.status is not JobStatus.PENDING:
            raise ValueError("only pending jobs may be submitted")
        if job.created_tick is None:
            job.created_tick = self.steps
        self.jobs[job.job_id] = job
        job.record(self.steps, "submitted")

    def dispatch_jobs(self) -> list[AuctionResult]:
        """Assign priority-ordered pending jobs to eligible idle robots."""

        results: list[AuctionResult] = []
        for job in self.pending_jobs:
            if not self.auctioneer.has_eligible_robot(self.robots.values()):
                break
            result = self.auctioneer.run(job, self.robots.values(), self.steps)
            self.auction_history.append(result)
            results.append(result)
            self._events.append({"type": "auction", **result.telemetry()})
            if result.winner_id is None:
                continue

            robot = self.robots[result.winner_id]
            job.status = JobStatus.ASSIGNED
            job.assigned_robot_id = robot.id
            if job.started_tick is None:
                job.started_tick = self.steps
            assignment_event = "reassigned" if job.reassignment_count else "assigned"
            job.assignment_history.append(robot.id)
            job.record(self.steps, assignment_event, robot.id)
            robot.assign_job(job)
        return results

    def inject_operational_failure(self, robot_id: str, reason: str = "blocked") -> None:
        """Make a robot unavailable and automatically recover its active job."""

        robot = self.robots[robot_id]
        if not robot.available:
            return
        job = robot.current_job
        carrying_package = bool(
            job
            and job.status in {JobStatus.PICKED_UP, JobStatus.TO_DROPOFF}
        )
        failure_position = robot.position
        robot.set_available(False, reason)
        self._events.append(
            {
                "type": "robot_unavailable",
                "tick": self.steps,
                "robot_id": robot.id,
                "reason": reason,
                "job_id": job.job_id if job else None,
                "position": failure_position,
            }
        )
        if job is not None:
            job.record(
                self.steps,
                "interrupted",
                robot.id,
                reason=reason,
                position=failure_position,
            )
            recovery_point = None
            if carrying_package:
                recovery_point = failure_position
                job.pickup = recovery_point
                job.record(
                    self.steps,
                    "package_recovery_point",
                    robot.id,
                    position=recovery_point,
                )
            robot.release_job_for_recovery()
            job.status = JobStatus.PENDING
            job.assigned_robot_id = None
            job.reassignment_count += 1
            job.failure_reason = None
            self._events.append(
                {
                    "type": "job_requeued",
                    "tick": self.steps,
                    "job_id": job.job_id,
                    "previous_robot_id": robot.id,
                    "package_recovery_point": recovery_point,
                }
            )
        self.dispatch_jobs()

    def set_robot_available(self, robot_id: str, available: bool) -> None:
        robot = self.robots[robot_id]
        if not available:
            self.inject_operational_failure(robot_id)
        else:
            robot.set_available(True)

    def pop_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def step(self) -> None:
        """Execute exactly one control/simulation tick for every robot."""

        self.dispatch_jobs()
        for robot in self.robots.values():
            robot.step(tick=self.steps)
            if robot.failed and robot.available:
                self.inject_operational_failure(robot.id, robot.failure_reason or "physical failure")
            self._events.extend(robot.pop_events())
        if (
            self.traffic_enabled
            and self.steps % self.traffic.config.evaluation_interval_ticks == 0
        ):
            self._events.extend(self.traffic.update(self.robots.values(), self.steps))
        self.steps += 1

    @property
    def all_arrived(self) -> bool:
        target_robots = [robot for robot in self.robots.values() if robot.target is not None]
        return bool(target_robots) and all(
            robot.arrived and not robot.failed for robot in target_robots
        )

    @property
    def failed(self) -> bool:
        return any(robot.failed for robot in self.robots.values())

    def telemetry(self) -> dict[str, dict[str, Any]]:
        return {robot_id: robot.telemetry() for robot_id, robot in self.robots.items()}

    def job_telemetry(self) -> dict[str, dict[str, Any]]:
        return {job_id: job.telemetry() for job_id, job in self.jobs.items()}

    def decision_telemetry(self) -> list[dict[str, Any]]:
        return [result.telemetry() for result in self.auction_history]

    def traffic_telemetry(self) -> list[dict[str, Any]]:
        return self.traffic.conflicts_telemetry()

    def stats(self) -> dict[str, int]:
        return {
            "jobs_completed": len(self.completed_jobs),
            "job_failures": len(self.failed_jobs),
            "robots_unavailable": sum(not robot.available for robot in self.robots.values()),
            "automatic_reassignments": sum(
                job.reassignment_count for job in self.jobs.values()
            ),
            "traffic_conflicts_prevented": self.traffic.conflicts_prevented,
            "manual_interventions": 0,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "tick": self.steps,
            "robots": self.telemetry(),
            "jobs": self.job_telemetry(),
            "auctions": self.decision_telemetry(),
            "conflicts": self.traffic_telemetry(),
            "stats": self.stats(),
        }
