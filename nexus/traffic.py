"""Low-cost trajectory prediction and logical multi-robot traffic control."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations
import math
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .robot import NexusRobot


@dataclass(frozen=True)
class TrafficConfig:
    safety_radius: float = 0.18
    nominal_speed: float = 0.15
    sample_interval_seconds: float = 0.10
    prediction_horizon_seconds: float = 15.0
    evaluation_interval_ticks: int = 40
    minimum_yield_ticks: int = 120
    clear_evaluations_to_resume: int = 2
    resume_cooldown_ticks: int = 160
    yield_timeout_ticks: int = 2_000

    @property
    def safe_separation(self) -> float:
        return 2.0 * self.safety_radius


@dataclass(frozen=True)
class PlannedTrajectory:
    robot_id: str
    current_position: tuple[float, float]
    target_position: tuple[float, float]
    estimated_path: tuple[tuple[float, float], ...]
    estimated_arrival_time: float
    job_priority: int
    nominal_speed: float

    def position_at(self, seconds: float) -> tuple[float, float]:
        remaining = max(0.0, seconds) * self.nominal_speed
        for start, end in zip(self.estimated_path, self.estimated_path[1:]):
            distance = math.dist(start, end)
            if distance <= 1e-12:
                continue
            if remaining <= distance:
                fraction = remaining / distance
                return (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
            remaining -= distance
        return self.target_position

    def telemetry(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "current_position": self.current_position,
            "target_position": self.target_position,
            "estimated_path": list(self.estimated_path),
            "estimated_arrival_time": self.estimated_arrival_time,
            "job_priority": self.job_priority,
        }


@dataclass(frozen=True)
class TrafficConflict:
    robot_a: str
    robot_b: str
    predicted_distance: float
    predicted_time: float
    conflict_point: tuple[float, float]
    right_of_way: str | None = None
    yielding_robot: str | None = None
    reason: str | None = None

    def telemetry(self) -> dict[str, Any]:
        return {
            "robot_a": self.robot_a,
            "robot_b": self.robot_b,
            "predicted_distance": self.predicted_distance,
            "predicted_time": self.predicted_time,
            "conflict_point": self.conflict_point,
            "right_of_way": self.right_of_way,
            "yielding_robot": self.yielding_robot,
            "reason": self.reason,
        }


def build_trajectory(
    robot: NexusRobot,
    config: TrafficConfig,
    *,
    target: tuple[float, float] | None = None,
) -> PlannedTrajectory | None:
    destination = target if target is not None else robot.planned_target
    if destination is None:
        return None
    start = robot.position
    path = robot.planned_path
    if target is not None:
        path = (start, target)
    elif not path:
        path = (start, destination)
    distance = sum(math.dist(first, second) for first, second in zip(path, path[1:]))
    arrival = distance / config.nominal_speed if distance else 0.0
    priority = robot.current_job.priority if robot.current_job is not None else 0
    return PlannedTrajectory(
        robot.id,
        start,
        path[-1],
        path,
        arrival,
        priority,
        config.nominal_speed,
    )


def predict_conflict(
    first: PlannedTrajectory,
    second: PlannedTrajectory,
    config: TrafficConfig | None = None,
) -> TrafficConflict | None:
    """Sample two constant-speed paths and return their closest unsafe approach."""

    settings = config or TrafficConfig()
    horizon = min(
        settings.prediction_horizon_seconds,
        max(first.estimated_arrival_time, second.estimated_arrival_time),
    )
    samples = max(1, math.ceil(horizon / settings.sample_interval_seconds))
    best_distance = math.inf
    best_time = 0.0
    best_first = first.current_position
    best_second = second.current_position

    for index in range(samples + 1):
        time = min(horizon, index * settings.sample_interval_seconds)
        first_position = first.position_at(time)
        second_position = second.position_at(time)
        distance = math.hypot(
            first_position[0] - second_position[0],
            first_position[1] - second_position[1],
        )
        if distance < best_distance:
            best_distance = distance
            best_time = time
            best_first = first_position
            best_second = second_position

    if best_distance >= settings.safe_separation:
        return None
    return TrafficConflict(
        first.robot_id,
        second.robot_id,
        best_distance,
        best_time,
        (
            (best_first[0] + best_second[0]) / 2.0,
            (best_first[1] + best_second[1]) / 2.0,
        ),
    )


def choose_right_of_way(
    first: NexusRobot,
    second: NexusRobot,
) -> tuple[NexusRobot, NexusRobot, str]:
    first_priority = first.current_job.priority if first.current_job else 0
    second_priority = second.current_job.priority if second.current_job else 0
    if first_priority != second_priority:
        if first_priority > second_priority:
            return first, second, "higher job priority"
        return second, first, "higher job priority"

    first_distance = first.planned_distance
    second_distance = second.planned_distance
    if not math.isclose(first_distance, second_distance, abs_tol=1e-9):
        if first_distance < second_distance:
            return first, second, "closer to finishing current leg"
        return second, first, "closer to finishing current leg"

    if first.id <= second.id:
        return first, second, "deterministic robot ID tie-breaker"
    return second, first, "deterministic robot ID tie-breaker"


class TrafficManager:
    """Evaluate path conflicts periodically and manage yield/resume hysteresis."""

    def __init__(self, config: TrafficConfig | None = None) -> None:
        self.config = config or TrafficConfig()
        self.active_conflicts: dict[str, TrafficConflict] = {}
        self.conflicts_prevented = 0
        self._yield_started: dict[str, int] = {}
        self._clear_counts: dict[str, int] = {}
        self._cooldown_until: dict[str, int] = {}

    def _path_is_clear(
        self,
        yielding: NexusRobot,
        robots: Iterable[NexusRobot],
    ) -> bool:
        intended = yielding.resume_target
        candidate = build_trajectory(yielding, self.config, target=intended)
        if candidate is None:
            return True
        for other in robots:
            if (
                other.id == yielding.id
                or not other.available
                or other.failed
                or other.yielding
                or not other.busy
            ):
                continue
            trajectory = build_trajectory(other, self.config)
            if trajectory and predict_conflict(candidate, trajectory, self.config):
                return False
        return True

    def update(self, robots: Iterable[NexusRobot], tick: int) -> list[dict[str, Any]]:
        robots_by_id = {robot.id: robot for robot in robots}
        events: list[dict[str, Any]] = []

        resumed_this_cycle: set[str] = set()
        for yielding_id, conflict in list(self.active_conflicts.items()):
            robot = robots_by_id.get(yielding_id)
            if robot is None or not robot.available or not robot.yielding:
                self.active_conflicts.pop(yielding_id, None)
                continue
            elapsed = tick - self._yield_started.get(yielding_id, tick)
            clear = self._path_is_clear(robot, robots_by_id.values())
            self._clear_counts[yielding_id] = (
                self._clear_counts.get(yielding_id, 0) + 1 if clear else 0
            )
            timed_out = elapsed >= self.config.yield_timeout_ticks
            sufficiently_clear = (
                elapsed >= self.config.minimum_yield_ticks
                and self._clear_counts[yielding_id]
                >= self.config.clear_evaluations_to_resume
            )
            if sufficiently_clear or timed_out:
                job_id = robot.current_job.job_id if robot.current_job else None
                robot.resume_from_yield(tick)
                events.append(
                    {
                        "type": "traffic_resumed",
                        "tick": tick,
                        "robot_id": robot.id,
                        "job_id": job_id,
                        "reason": "yield timeout" if timed_out else "path clear",
                    }
                )
                self.active_conflicts.pop(yielding_id, None)
                self._clear_counts.pop(yielding_id, None)
                self._yield_started.pop(yielding_id, None)
                self._cooldown_until[yielding_id] = tick + self.config.resume_cooldown_ticks
                resumed_this_cycle.add(yielding_id)

        moving = [
            robot
            for robot in robots_by_id.values()
            if robot.available
            and not robot.failed
            and robot.busy
            and not robot.yielding
            and robot.planned_target is not None
            and robot.id not in resumed_this_cycle
        ]
        trajectories = {
            robot.id: build_trajectory(robot, self.config) for robot in moving
        }
        for first, second in combinations(moving, 2):
            if first.yielding or second.yielding:
                continue
            if tick < self._cooldown_until.get(first.id, 0) or tick < self._cooldown_until.get(
                second.id, 0
            ):
                continue
            first_path = trajectories[first.id]
            second_path = trajectories[second.id]
            if first_path is None or second_path is None:
                continue
            conflict = predict_conflict(first_path, second_path, self.config)
            if conflict is None:
                continue
            winner, loser, reason = choose_right_of_way(first, second)
            loser.begin_yield(
                f"yielding to {winner.id}: {reason}",
                tick,
            )
            decided = replace(
                conflict,
                right_of_way=winner.id,
                yielding_robot=loser.id,
                reason=reason,
            )
            self.active_conflicts[loser.id] = decided
            self._yield_started[loser.id] = tick
            self._clear_counts[loser.id] = 0
            self.conflicts_prevented += 1
            events.append({"type": "traffic_conflict", "tick": tick, **decided.telemetry()})
        return events

    def conflicts_telemetry(self) -> list[dict[str, Any]]:
        return [conflict.telemetry() for conflict in self.active_conflicts.values()]
