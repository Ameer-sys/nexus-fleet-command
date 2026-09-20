"""Reusable deterministic NEXUS warehouse story for console and dashboard demos."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from .fleet import Fleet
from .jobs import Job, JobStatus
from .navigation import NavigationConfig, NavigationController
from .robot import NexusRobot
from .solana import ChainStatus, SolanaCustodyLedger
from .traffic import TrafficConfig, TrafficManager, build_trajectory
from .warehouse import (
    WarehouseDestination,
    WarehouseNavigationMap,
    WarehousePackage,
    create_destinations,
    create_packages,
    create_stations,
)


PRIORITY_REQUEST_TICK = 1_200
FAILURE_DELAY_AFTER_PICKUP = 200


@dataclass(frozen=True)
class NexusEvent:
    tick: int
    category: str
    message: str
    robot_ids: tuple[str, ...] = ()
    job_id: str | None = None
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def telemetry(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "time": self.tick / 200.0,
            "category": self.category,
            "message": self.message,
            "robot_ids": list(self.robot_ids),
            "job_id": self.job_id,
            "severity": self.severity,
            "details": dict(self.details),
        }


class WarehouseDemo:
    """Own the known-good milestone #3 scenario and its presentation state."""

    def __init__(
        self,
        demo_speed: float = 0.65,
        *,
        custody_ledger: SolanaCustodyLedger | None = None,
        mode: str = "scripted",
    ) -> None:
        if mode not in {"manual", "scripted"}:
            raise ValueError("mode must be manual or scripted")
        self.mode = mode
        self.demo_speed = max(0.05, min(5.0, float(demo_speed)))
        self.manual_interventions = 0
        self.running = False
        self.finished = False
        self.priority_submitted = False
        self.failure_injected = False
        self.events: list[NexusEvent] = []
        self.packages = create_packages()
        self.destinations = create_destinations()
        self.stations = create_stations()
        self.navigation_map = WarehouseNavigationMap()
        self.last_decision: dict[str, Any] | None = None
        self.decision_history: list[dict[str, Any]] = []
        self._manual_job_counter = 0
        self.custody_ledger = custody_ledger or SolanaCustodyLedger()
        self._custody_events_recorded: set[str] = set()
        self._chain_status_seen: dict[int, str] = {}
        self.fleet = self._create_fleet(
            self.navigation_map,
            self.stations.values(),
            include_scripted_jobs=mode == "scripted",
        )
        self.urgent = Job(
            "JOB-URGENT",
            "MEDICAL-CRITICAL",
            pickup=(0.82, 0.98),
            dropoff=(0.82, 0.08),
            priority=10,
        )
        self._record("SYSTEM", "NEXUS control center ready", severity="success")
        if self.custody_ledger.enabled:
            wallet = self.custody_ledger.wallet_address or "unknown"
            self._record(
                "SOLANA",
                f"Devnet ready · wallet {wallet[:4]}…{wallet[-4:]}",
                severity="success",
            )
        else:
            self._record("SOLANA", "Devnet offline · demo continues", severity="warning")

    @staticmethod
    def _create_fleet(
        navigation_map: WarehouseNavigationMap,
        stations,
        *,
        include_scripted_jobs: bool = True,
    ) -> Fleet:
        traffic = TrafficManager(
            TrafficConfig(
                safety_radius=0.16,
                nominal_speed=0.15,
                evaluation_interval_ticks=40,
                minimum_yield_ticks=160,
                clear_evaluations_to_resume=2,
                resume_cooldown_ticks=200,
                yield_timeout_ticks=2_400,
            )
        )
        controller = (
            NavigationController(
                NavigationConfig(
                    max_forward_velocity=0.24,
                    distance_kp=0.50,
                )
            )
            if include_scripted_jobs
            else None
        )
        profiles = (
            ((96, 98), (74, 82), (99, 97))
            if include_scripted_jobs
            else ((100, 100), (74, 82), (88, 90))
        )
        robots = [
            NexusRobot(
                robot_id,
                controller=controller,
                health_percent=health,
                reliability_score=reliability,
                navigation_map=navigation_map,
            )
            for robot_id, (health, reliability) in zip(("A", "B", "C"), profiles)
        ]
        station_positions = ((0.08, 1.92), (1.92, 1.92), (0.08, 0.08))
        for robot, desired in zip(robots, station_positions):
            current = robot.position
            robot.offset = (desired[0] - current[0], desired[1] - current[1])
        fleet = Fleet(
            robots,
            traffic_manager=traffic,
            stations=stations,
        )
        fleet.initialize_station_occupancy("A", "STATION-NW")
        fleet.initialize_station_occupancy("B", "STATION-NE")
        fleet.initialize_station_occupancy("C", "STATION-SW")
        if include_scripted_jobs:
            for job in (
                Job("JOB-A", "PRIORITY-PART", (0.18, 0.08), (0.18, 0.98), priority=8),
                Job("JOB-B", "STANDARD-BOX", (1.8, 0.1), (0.2, 1.8), priority=3),
                Job("JOB-C", "SENSOR-KIT", (0.18, 1.92), (0.82, 0.98), priority=5),
            ):
                fleet.submit_job(job)
        return fleet

    def reset(self, mode: str | None = None) -> None:
        speed = self.demo_speed
        next_mode = mode or self.mode
        self.custody_ledger.close()
        replacement = WarehouseDemo(speed, mode=next_mode)
        self.__dict__.update(replacement.__dict__)
        message = "Warehouse reset — select a package" if next_mode == "manual" else "Demo reset — ready to start"
        self._record("SYSTEM", message, severity="success")

    def start(self) -> None:
        if self.mode != "scripted":
            self.reset("scripted")
        if self.finished:
            self.reset()
        self.running = True
        self._record("SYSTEM", "Autonomous warehouse demo started", severity="success")

    def reset_warehouse(self) -> None:
        self.reset("manual")

    def submit_fleet_command(
        self,
        package_id: str,
        destination_id: str,
        *,
        category: str | None = None,
        priority: int | None = None,
    ) -> Job:
        if self.mode != "manual":
            self.reset("manual")
        package, destination, handling, command_priority = self._validate_fleet_command(
            package_id,
            destination_id,
            category=category,
            priority=priority,
        )

        self._manual_job_counter += 1
        job = Job(
            f"CMD-{self._manual_job_counter:03d}",
            package.package_id,
            package.access_position or package.position,
            destination.approach_position or destination.position,
            priority=command_priority,
            handling_category=handling,
            risk=package.risk,
            destination_id=destination.destination_id,
            manual_command=True,
        )
        package.status = "QUEUED"
        package.active_job_id = job.job_id
        self.finished = False
        self.running = True
        self.fleet.submit_job(job)
        self._record(
            "COMMAND",
            f"{package.name} → {destination.name}",
            job_id=job.job_id,
            severity="critical" if command_priority >= 9 else "info",
            details={"package_id": package_id, "destination_id": destination_id, "category": handling},
        )
        self._record("AUCTION", "Evaluating Robots A, B, C", job_id=job.job_id)
        self.fleet.dispatch_jobs()
        for event in self.fleet.pop_events():
            self._translate_event(event)
        if job.assigned_robot_id is None:
            preview = self.fleet.auctioneer.run(job, self.fleet.robots.values(), self.fleet.steps)
            pending = self.fleet.pending_jobs
            queue_position = pending.index(job) + 1
            self.last_decision = {
                "type": "QUEUED",
                "job_id": job.job_id,
                "package_id": job.package_id,
                "category": job.handling_category,
                "winner": None,
                "bids": [bid.telemetry() for bid in preview.bids],
                "reasons": ["All fleet agents currently occupied"],
                "comparison": f"Priority {job.priority} · queue position {queue_position}",
                "queue_position": queue_position,
                "tick": self.fleet.steps,
            }
            self.decision_history.append(self.last_decision)
            self._record(
                "QUEUE",
                f"{job.job_id} queued · all fleet agents occupied · position {queue_position}",
                job_id=job.job_id,
                severity="warning",
                details=self.last_decision,
            )
        return job

    def submit_fleet_batch(
        self,
        items: list[tuple[str, str]],
        *,
        priority: int | None = None,
    ) -> list[Job]:
        """Validate atomically, then submit normal jobs through the existing fleet path."""

        if self.mode != "manual":
            self.reset("manual")
        if not items:
            raise ValueError("batch must contain at least one item")
        package_ids = [package_id for package_id, _ in items]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("duplicate package in batch")
        for package_id, destination_id in items:
            self._validate_fleet_command(
                package_id,
                destination_id,
                category=None,
                priority=priority,
            )
        return [
            self.submit_fleet_command(
                package_id,
                destination_id,
                priority=priority,
            )
            for package_id, destination_id in items
        ]

    def _validate_fleet_command(
        self,
        package_id: str,
        destination_id: str,
        *,
        category: str | None,
        priority: int | None,
    ) -> tuple[WarehousePackage, WarehouseDestination, str, int]:
        package = self.packages.get(package_id)
        destination = self.destinations.get(destination_id)
        if package is None:
            raise ValueError(f"unknown package: {package_id}")
        if destination is None:
            raise ValueError(f"unknown destination: {destination_id}")
        if package.status != "AVAILABLE":
            raise ValueError(f"package {package_id} is already {package.status.lower()}")
        handling = (category or package.category).upper()
        if handling not in {"STANDARD", "FRAGILE", "MEDICAL", "HIGH_VALUE"}:
            raise ValueError("category must be STANDARD, FRAGILE, MEDICAL, or HIGH_VALUE")
        command_priority = package.priority if priority is None else int(priority)
        if not 1 <= command_priority <= 10:
            raise ValueError("priority must be between 1 and 10")
        return package, destination, handling, command_priority

    def _build_decision(self, job: Job, event: dict[str, Any]) -> dict[str, Any]:
        winner_id = event["winner"]
        bids = event["bids"]
        winner = next(bid for bid in bids if bid["robot_id"] == winner_id)
        eligible = [bid for bid in bids if bid["eligible"]]
        nearest = min(eligible, key=lambda bid: (bid["distance"], bid["robot_id"])) if eligible else winner
        components = winner["components"]
        reasons = [
            f"{components['health']['value']:.0f}% system health",
            f"{components['reliability']['value']:.0f}% reliability",
            f"{components['battery']['value']:.0f}% battery",
            f"{components['traffic']['value'].lower()} traffic risk",
            f"optimized for {job.handling_category.replace('_', ' ').lower()} cargo",
        ]
        if nearest["robot_id"] != winner_id:
            comparison = (
                f"Robot {nearest['robot_id']} was closer, but Robot {winner_id}'s health, "
                "reliability, and battery produced the lower mission cost."
            )
        else:
            comparison = f"Robot {winner_id} had the lowest total mission cost."
        return {
            "type": "RECOVERY_DECISION" if job.reassignment_count else "NEXUS_DECISION",
            "job_id": job.job_id,
            "package_id": job.package_id,
            "category": job.handling_category,
            "winner": winner_id,
            "bids": bids,
            "reasons": reasons,
            "comparison": comparison,
            "tick": self.fleet.steps,
        }

    def pause(self) -> None:
        self.running = False
        self._record("SYSTEM", "Demo paused")

    def _record(
        self,
        category: str,
        message: str,
        *,
        robot_ids: tuple[str, ...] = (),
        job_id: str | None = None,
        severity: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            NexusEvent(
                self.fleet.steps,
                category,
                message,
                robot_ids,
                job_id,
                severity,
                details or {},
            )
        )
        self.events = self.events[-200:]

    def submit_priority_job(self, *, manual: bool = False) -> bool:
        if self.priority_submitted:
            return False
        self.fleet.submit_job(self.urgent)
        self.priority_submitted = True
        if manual:
            self.manual_interventions += 1
        self._record(
            "PRIORITY",
            "MEDICAL-CRITICAL submitted at priority 10",
            job_id=self.urgent.job_id,
            severity="critical",
        )
        self._record_custody("JOB_CREATED")
        return True

    def _record_custody(
        self,
        event_type: str,
        *,
        robot_id: str | None = None,
        previous_robot_id: str | None = None,
        position: tuple[float, float] | list[float] | None = None,
    ) -> None:
        """Queue one on-chain business event for the critical package."""

        if event_type in self._custody_events_recorded:
            return
        self._custody_events_recorded.add(event_type)
        normalized_position = None
        if position is not None:
            normalized_position = [round(float(position[0]), 3), round(float(position[1]), 3)]
        record = self.custody_ledger.record_event(
            protocol="NEXUS",
            version=1,
            job_id=self.urgent.job_id,
            package_id=self.urgent.package_id,
            event=event_type,
            robot_id=robot_id,
            previous_robot_id=previous_robot_id,
            tick=self.fleet.steps,
            position=normalized_position,
        )
        self._chain_status_seen[record.sequence] = record.chain_status.value
        suffix = "queued" if record.chain_status is ChainStatus.PENDING else "not submitted · demo continues"
        self._record(
            "SOLANA",
            f"{event_type} {suffix}",
            robot_ids=(robot_id,) if robot_id else (),
            job_id=self.urgent.job_id,
            severity="info" if record.chain_status is ChainStatus.PENDING else "warning",
            details={"chain_status": record.chain_status.value},
        )

    def _sync_custody_events(self) -> None:
        for record in self.custody_ledger.records(self.urgent.job_id):
            sequence = record["sequence"]
            status = record["chain_status"]
            if self._chain_status_seen.get(sequence) == status:
                continue
            self._chain_status_seen[sequence] = status
            if status == ChainStatus.CONFIRMED.value:
                signature = record["transaction_signature"]
                self._record(
                    "SOLANA",
                    f"{record['event']} confirmed · tx {signature[:6]}…{signature[-6:]}",
                    job_id=self.urgent.job_id,
                    severity="success",
                    details={
                        "chain_status": status,
                        "transaction_signature": signature,
                        "explorer_url": record["explorer_url"],
                    },
                )
            elif status == ChainStatus.FAILED.value:
                self._record(
                    "SOLANA",
                    f"{record['event']} failed · demo continues",
                    job_id=self.urgent.job_id,
                    severity="warning",
                    details={"chain_status": status, "error": record["error"]},
                )

    def inject_failure(self, robot_id: str, *, manual: bool = False) -> bool:
        robot = self.fleet.robots.get(robot_id)
        if robot is None or not robot.available:
            return False
        active_job = robot.current_job
        self.fleet.inject_operational_failure(robot_id, "blocked aisle")
        if manual:
            self.manual_interventions += 1
        if active_job is self.urgent:
            self.failure_injected = True
        elif active_job is not None and active_job.manual_command:
            self.failure_injected = True
        for event in self.fleet.pop_events():
            self._translate_event(event)
        return True

    def _automatic_failure_ready(self) -> bool:
        to_dropoff = next(
            (event for event in reversed(self.urgent.history) if event.event == "to_dropoff"),
            None,
        )
        return bool(
            self.priority_submitted
            and not self.failure_injected
            and self.urgent.status is JobStatus.TO_DROPOFF
            and to_dropoff is not None
            and self.fleet.steps - to_dropoff.tick >= FAILURE_DELAY_AFTER_PICKUP
            and self.urgent.assigned_robot_id is not None
        )

    def _translate_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == "auction":
            job = self.fleet.jobs[event["job_id"]]
            if event["winner"]:
                if job.manual_command and job.created_tick is not None and self.fleet.steps > job.created_tick:
                    self._record("AUCTION", f"Re-evaluating {job.job_id}", job_id=job.job_id)
                category = "RECOVERY" if job.reassignment_count else "AUCTION"
                if job.reassignment_count and len(job.assignment_history) >= 2:
                    message = (
                        f"{job.job_id} reassigned "
                        f"{job.assignment_history[-2]} → {event['winner']}"
                    )
                else:
                    message = f"{job.job_id} assigned to Robot {event['winner']}"
                self._record(
                    category,
                    message,
                    robot_ids=(event["winner"],),
                    job_id=job.job_id,
                    severity="success",
                    details={"bids": event["bids"]},
                )
                if job.manual_command:
                    self.last_decision = self._build_decision(job, event)
                    self.decision_history.append(self.last_decision)
                    self._record(
                        "DECISION",
                        f"Robot {event['winner']} selected for {job.package_id}",
                        robot_ids=(event["winner"],),
                        job_id=job.job_id,
                        severity="success",
                        details=self.last_decision,
                    )
                    package = self.packages[job.package_id]
                    package.status = "TO_PICKUP"
                    package.current_custodian = event["winner"]
                if job is self.urgent:
                    if job.reassignment_count and len(job.assignment_history) >= 2:
                        self._record_custody(
                            "CUSTODY_TRANSFER",
                            robot_id=event["winner"],
                            previous_robot_id=job.assignment_history[-2],
                            position=job.pickup,
                        )
                    else:
                        self._record_custody(
                            "CUSTODY_ASSIGNED",
                            robot_id=event["winner"],
                            position=job.pickup,
                        )
        elif event_type == "traffic_conflict":
            self._record(
                "TRAFFIC",
                f"Conflict {event['robot_a']} ↔ {event['robot_b']}; "
                f"{event['right_of_way']} has right-of-way",
                robot_ids=(event["robot_a"], event["robot_b"]),
                severity="warning",
                details=event,
            )
            self._record(
                "TRAFFIC",
                f"Robot {event['yielding_robot']} yielding",
                robot_ids=(event["yielding_robot"],),
                severity="warning",
            )
        elif event_type == "traffic_resumed":
            self._record(
                "TRAFFIC",
                f"Path clear — Robot {event['robot_id']} resumed",
                robot_ids=(event["robot_id"],),
                severity="success",
            )
        elif event_type == "package_acquired":
            self._record(
                "JOB",
                f"Robot {event['robot_id']} acquired {event['package_id']}",
                robot_ids=(event["robot_id"],),
                job_id=event["job_id"],
            )
            if event["job_id"] == self.urgent.job_id:
                self._record_custody(
                    "PACKAGE_PICKED_UP",
                    robot_id=event["robot_id"],
                    position=self.fleet.robots[event["robot_id"]].position,
                )
            job = self.fleet.jobs[event["job_id"]]
            if job.manual_command:
                package = self.packages[job.package_id]
                package.status = "IN_TRANSIT"
                package.current_custodian = event["robot_id"]
                package.position = self.fleet.robots[event["robot_id"]].position
                self._record(
                    "PICKUP",
                    f"Robot {event['robot_id']} acquired {package.name}",
                    robot_ids=(event["robot_id"],),
                    job_id=job.job_id,
                    severity="success",
                )
        elif event_type == "delivery_started":
            self._record(
                "JOB",
                f"{event['job_id']} delivery leg started",
                robot_ids=(event["robot_id"],),
                job_id=event["job_id"],
            )
        elif event_type == "job_completed":
            self._record(
                "JOB",
                f"{event['job_id']} completed by Robot {event['robot_id']}",
                robot_ids=(event["robot_id"],),
                job_id=event["job_id"],
                severity="success",
            )
            if event["job_id"] == self.urgent.job_id:
                self._record_custody(
                    "PACKAGE_DELIVERED",
                    robot_id=event["robot_id"],
                    position=self.fleet.robots[event["robot_id"]].position,
                )
            job = self.fleet.jobs[event["job_id"]]
            if job.manual_command:
                package = self.packages[job.package_id]
                destination = self.destinations[job.destination_id]
                package.status = "DELIVERED"
                package.position = destination.position
                package.location = destination.name
                package.previous_custodian = package.current_custodian
                package.current_custodian = None
                self._record(
                    "DELIVERY",
                    f"{package.name} delivered to {destination.name}",
                    robot_ids=(event["robot_id"],),
                    job_id=job.job_id,
                    severity="success",
                )
        elif event_type == "station_returning":
            station = self.stations[event["station_id"]]
            self._record(
                "FLEET",
                f"Robot {event['robot_id']} returning to {station.label}",
                robot_ids=(event["robot_id"],),
                severity="info",
                details={"station_id": station.station_id},
            )
        elif event_type == "station_parked":
            station = self.stations[event["station_id"]]
            self._record(
                "FLEET",
                f"Robot {event['robot_id']} parked at {station.label}",
                robot_ids=(event["robot_id"],),
                severity="success",
                details={"station_id": station.station_id},
            )
        elif event_type == "robot_unavailable":
            self._record(
                "FAULT",
                f"Robot {event['robot_id']} unavailable — {event['reason']}",
                robot_ids=(event["robot_id"],),
                job_id=event.get("job_id"),
                severity="critical",
                details={"position": event["position"]},
            )
            if event.get("job_id") == self.urgent.job_id:
                self._record_custody(
                    "ROBOT_UNAVAILABLE",
                    robot_id=event["robot_id"],
                    position=event["position"],
                )
            job = self.fleet.jobs.get(event.get("job_id"))
            if job is not None and job.manual_command:
                package = self.packages[job.package_id]
                package.previous_custodian = event["robot_id"]
                package.current_custodian = None
        elif event_type == "job_requeued":
            point = event.get("package_recovery_point")
            message = f"{event['job_id']} requeued automatically"
            if point:
                message += f" from recovery point ({point[0]:.2f}, {point[1]:.2f})"
            self._record(
                "RECOVERY",
                message,
                robot_ids=(event["previous_robot_id"],),
                job_id=event["job_id"],
                severity="warning",
                details={"recovery_point": point},
            )
            if event["job_id"] == self.urgent.job_id and point:
                self._record_custody(
                    "RECOVERY_POINT_CREATED",
                    robot_id=event["previous_robot_id"],
                    position=point,
                )
            job = self.fleet.jobs[event["job_id"]]
            if job.manual_command and point:
                package = self.packages[job.package_id]
                package.status = "RECOVERY_PENDING"
                package.position = tuple(point)
                self._record(
                    "RECOVERY",
                    f"{package.name} recovery point created",
                    job_id=job.job_id,
                    severity="warning",
                    details={"recovery_point": point},
                )
            elif job.manual_command:
                package = self.packages[job.package_id]
                package.status = "QUEUED"

    def step(self) -> None:
        if not self.running or self.finished:
            return
        if self.mode == "scripted" and self.fleet.steps == PRIORITY_REQUEST_TICK:
            self.submit_priority_job()
        if self.mode == "scripted" and self._automatic_failure_ready():
            robot_id = self.urgent.assigned_robot_id
            if robot_id is not None:
                self.inject_failure(robot_id)

        self.fleet.step()
        for event in self.fleet.pop_events():
            self._translate_event(event)
        self._sync_custody_events()

        if (
            self.mode == "scripted"
            and self.priority_submitted
            and self.failure_injected
            and self.fleet.all_jobs_finished
            and self.fleet.all_robots_parked
        ):
            self.finished = True
            self.running = False
            self._record(
                "SYSTEM",
                "Autonomous warehouse run completed",
                severity="success",
            )
        elif (
            self.mode == "manual"
            and self.fleet.jobs
            and self.fleet.all_jobs_finished
            and self.fleet.all_robots_parked
        ):
            self.finished = True
            self.running = False
            self._record(
                "SYSTEM",
                "DELIVERY COMPLETE · 0 MANUAL ROUTING ACTIONS",
                severity="success",
            )

    def _robot_dashboard(self, robot: NexusRobot) -> dict[str, Any]:
        item = robot.telemetry()
        if robot.failed:
            state = "FAILED"
        elif not robot.available:
            state = "UNAVAILABLE"
        elif robot.yielding:
            state = "YIELDING"
        elif robot.task_state.value == "RETURNING_TO_STATION":
            state = "RETURNING"
        elif robot.task_state.value == "PARKED":
            state = "PARKED"
        elif robot.busy:
            state = "ACTIVE"
        else:
            state = "AVAILABLE"
        item["state"] = state
        item["position"] = [item["x"], item["y"]]
        return item

    @staticmethod
    def _job_dashboard(job: Job) -> dict[str, Any]:
        item = job.telemetry()
        recovery = next(
            (
                event.details.get("position")
                for event in reversed(job.history)
                if event.event == "package_recovery_point"
            ),
            None,
        )
        item["recovery_point"] = recovery
        return item

    def snapshot(self) -> dict[str, Any]:
        self._sync_custody_events()
        if self.mode == "manual":
            for package in self.packages.values():
                if package.current_custodian:
                    robot = self.fleet.robots.get(package.current_custodian)
                    if robot is not None and package.status == "IN_TRANSIT":
                        package.position = robot.position
        robots = [self._robot_dashboard(robot) for robot in self.fleet]
        jobs = [self._job_dashboard(job) for job in self.fleet.jobs.values()]
        queue_positions = {job.job_id: index for index, job in enumerate(self.fleet.pending_jobs, 1)}
        for job in jobs:
            job["custody"] = self.custody_ledger.records(job["job_id"])
            job["queue_position"] = queue_positions.get(job["job_id"])
        trajectories = []
        for robot in self.fleet:
            trajectory = build_trajectory(robot, self.fleet.traffic.config)
            if trajectory is not None:
                item = trajectory.telemetry()
                item["yielding"] = robot.yielding
                trajectories.append(item)

        stats = self.fleet.stats()
        stats.update(
            {
                "robots_online": sum(robot.available and not robot.failed for robot in self.fleet),
                "robots_total": len(self.fleet.robots),
                "active_jobs": sum(
                    job.status
                    in {
                        JobStatus.ASSIGNED,
                        JobStatus.TO_PICKUP,
                        JobStatus.PICKED_UP,
                        JobStatus.TO_DROPOFF,
                    }
                    for job in self.fleet.jobs.values()
                ),
                "pending_jobs": len(self.fleet.pending_jobs),
                "active_job_count": sum(
                    job.status
                    in {JobStatus.ASSIGNED, JobStatus.TO_PICKUP, JobStatus.PICKED_UP, JobStatus.TO_DROPOFF}
                    for job in self.fleet.jobs.values()
                ),
                "completed_job_count": len(self.fleet.completed_jobs),
                "robots_busy": sum(robot.busy for robot in self.fleet),
                "robots_available": sum(robot.available and not robot.busy for robot in self.fleet),
                "manual_interventions": self.manual_interventions,
                "manual_routing_actions": 0,
            }
        )
        phase = "READY"
        if self.finished:
            phase = "DELIVERY COMPLETE" if self.mode == "manual" else "COMPLETE"
        elif self.failure_injected:
            phase = "RECOVERY"
        elif self.priority_submitted:
            phase = "PRIORITY RESPONSE"
        elif self.running:
            phase = "COMMAND EXECUTION" if self.mode == "manual" else "AUTONOMOUS OPERATIONS"
        return {
            "simulation": {
                "tick": self.fleet.steps,
                "time": self.fleet.steps / 200.0,
                "running": self.running,
                "finished": self.finished,
                "demo_speed": self.demo_speed,
                "phase": phase,
                "mode": self.mode,
            },
            "warehouse": {
                "x_min": 0.0,
                "x_max": 2.0,
                "y_min": 0.0,
                "y_max": 2.0,
                "navigation": self.navigation_map.telemetry(),
                "stations": [station.telemetry() for station in self.stations.values()],
            },
            "robots": robots,
            "jobs": jobs,
            "packages": [package.telemetry() for package in self.packages.values()] if self.mode == "manual" else [],
            "destinations": [destination.telemetry() for destination in self.destinations.values()] if self.mode == "manual" else [],
            "decision": self.last_decision,
            "decision_history": list(self.decision_history[-10:]),
            "trajectories": trajectories,
            "conflicts": self.fleet.traffic_telemetry(),
            "events": [event.telemetry() for event in self.events[-80:]],
            "stats": stats,
            "solana": self.custody_ledger.get_status(),
        }

    def close(self) -> None:
        self.custody_ledger.close()
