"""Warehouse job data and lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    TO_PICKUP = "TO_PICKUP"
    PICKED_UP = "PICKED_UP"
    TO_DROPOFF = "TO_DROPOFF"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskState(str, Enum):
    IDLE = "IDLE"
    PARKED = "PARKED"
    RETURNING_TO_STATION = "RETURNING_TO_STATION"
    TO_PICKUP = "TO_PICKUP"
    PICKED_UP = "PICKED_UP"
    TO_DROPOFF = "TO_DROPOFF"
    YIELDING = "YIELDING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class JobEvent:
    tick: int
    event: str
    robot_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def telemetry(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "event": self.event,
            "robot_id": self.robot_id,
            "details": dict(self.details),
        }


@dataclass
class Job:
    """A logical package movement request in global warehouse coordinates."""

    job_id: str
    package_id: str
    pickup: tuple[float, float]
    dropoff: tuple[float, float]
    priority: int = 5
    value: float | None = None
    handling_category: str = "STANDARD"
    risk: str = "LOW"
    destination_id: str | None = None
    manual_command: bool = False
    status: JobStatus = JobStatus.PENDING
    assigned_robot_id: str | None = None
    created_tick: int | None = None
    started_tick: int | None = None
    completed_tick: int | None = None
    failure_reason: str | None = None
    reassignment_count: int = 0
    assignment_history: list[str] = field(default_factory=list)
    history: list[JobEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.job_id = str(self.job_id)
        self.package_id = str(self.package_id)
        self.pickup = (float(self.pickup[0]), float(self.pickup[1]))
        self.dropoff = (float(self.dropoff[0]), float(self.dropoff[1]))
        if not 1 <= self.priority <= 10:
            raise ValueError("job priority must be between 1 and 10")
        if self.value is not None:
            self.value = float(self.value)
        self.handling_category = str(self.handling_category).upper()
        self.risk = str(self.risk).upper()

    @property
    def duration_ticks(self) -> int | None:
        if self.started_tick is None or self.completed_tick is None:
            return None
        return self.completed_tick - self.started_tick

    def record(
        self,
        tick: int,
        event: str,
        robot_id: str | None = None,
        **details: Any,
    ) -> None:
        self.history.append(JobEvent(tick, event, robot_id, details))

    def telemetry(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "package_id": self.package_id,
            "priority": self.priority,
            "value": self.value,
            "handling_category": self.handling_category,
            "risk": self.risk,
            "destination_id": self.destination_id,
            "manual_command": self.manual_command,
            "status": self.status.value,
            "assigned_robot": self.assigned_robot_id,
            "pickup": self.pickup,
            "dropoff": self.dropoff,
            "created_tick": self.created_tick,
            "started_tick": self.started_tick,
            "completed_tick": self.completed_tick,
            "duration_ticks": self.duration_ticks,
            "failure_reason": self.failure_reason,
            "reassignment_count": self.reassignment_count,
            "assignment_history": list(self.assignment_history),
            "history": [event.telemetry() for event in self.history],
        }
