"""Fleet orchestration for independent NEXUS robot simulations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from .auction import AuctionResult, Auctioneer
from .jobs import Job, JobStatus
from .robot import NexusRobot
from .scheduler import JobScheduler
from .traffic import TrafficManager
from .warehouse import WarehouseStation


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
        stations: Iterable[WarehouseStation] = (),
    ) -> None:
        self.robots: dict[str, NexusRobot] = {}
        self.jobs: dict[str, Job] = {}
        self.auctioneer = auctioneer or Auctioneer()
        self.scheduler = scheduler or JobScheduler()
        self.traffic = traffic_manager or TrafficManager()
        self.traffic_enabled = traffic_enabled
        self.stations = {station.station_id: station for station in stations}
        self.auction_history: list[AuctionResult] = []
        self._events: list[dict[str, Any]] = []
        self.steps = 0
        for robot in robots:
            self.add(robot)

    def add(self, robot: NexusRobot) -> None:
        if robot.id in self.robots:
            raise ValueError(f"duplicate robot id: {robot.id}")
        self.robots[robot.id] = robot

    def initialize_station_occupancy(self, robot_id: str, station_id: str) -> None:
        robot = self.robots[robot_id]
        station = self.stations[station_id]
        station.occupy(robot_id)
        robot.initialize_parked(station_id)

    def _release_robot_station(self, robot: NexusRobot) -> None:
        if robot.station_id is not None:
            station = self.stations.get(robot.station_id)
            if station is not None:
                station.release(robot.id)
        robot.leave_station()

    def select_station(self, robot: NexusRobot) -> WarehouseStation | None:
        available = [
            station
            for station in self.stations.values()
            if station.status == "AVAILABLE"
            and station.occupied_by is None
            and station.reserved_by is None
        ]
        if not available:
            return None

        def distance(station: WarehouseStation) -> tuple[float, str]:
            if robot.navigation_map is not None:
                route_distance = robot.navigation_map.route_distance(
                    robot.position,
                    station.position,
                )
            else:
                from math import dist

                route_distance = dist(robot.position, station.position)
            return route_distance, station.station_id

        return min(available, key=distance)

    def return_robot_to_station(self, robot_id: str) -> WarehouseStation | None:
        robot = self.robots[robot_id]
        if not self.stations or not robot.available or robot.current_job is not None:
            return None
        if robot.task_state.value in {"PARKED", "RETURNING_TO_STATION"}:
            return self.stations.get(robot.station_id or "")
        station = self.select_station(robot)
        if station is None:
            return None
        station.reserve(robot.id)
        robot.start_station_return(station.station_id, station.position)
        self._events.append(
            {
                "type": "station_returning",
                "tick": self.steps,
                "robot_id": robot.id,
                "station_id": station.station_id,
                "position": station.position,
            }
        )
        return station

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
            self._release_robot_station(robot)
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
        self._release_robot_station(robot)
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
        completed_robots: list[NexusRobot] = []
        for robot in self.robots.values():
            robot.step(tick=self.steps)
            if robot.failed and robot.available:
                self.inject_operational_failure(robot.id, robot.failure_reason or "physical failure")
            robot_events = robot.pop_events()
            for event in robot_events:
                if event["type"] == "job_completed":
                    completed_robots.append(robot)
                elif event["type"] == "station_parked":
                    station = self.stations.get(event["station_id"])
                    if station is not None:
                        station.occupy(robot.id)
            self._events.extend(robot_events)
        if completed_robots:
            self.dispatch_jobs()
            for robot in completed_robots:
                if robot.current_job is None and robot.available:
                    self.return_robot_to_station(robot.id)
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

    @property
    def all_robots_parked(self) -> bool:
        return all(
            not robot.available or robot.task_state.value == "PARKED"
            for robot in self.robots.values()
        )

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
            "stations": [station.telemetry() for station in self.stations.values()],
        }
