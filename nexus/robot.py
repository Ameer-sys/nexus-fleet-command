"""Robot abstraction joining one Bracket Bot simulation to NEXUS navigation."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from bbsim.simulation import Simulation

from .jobs import Job, JobStatus, TaskState
from .navigation import NavigationController

if TYPE_CHECKING:
    from .warehouse import WarehouseNavigationMap


class NexusRobot:
    """One independently simulated robot in the shared warehouse frame."""

    def __init__(
        self,
        robot_id: str,
        offset: tuple[float, float] = (0.0, 0.0),
        *,
        simulation: Simulation | None = None,
        controller: NavigationController | None = None,
        battery: float = 100.0,
        battery_drain_per_second: float = 0.002,
        health_percent: float = 100.0,
        reliability_score: float = 100.0,
        navigation_map: WarehouseNavigationMap | None = None,
    ) -> None:
        self.id = str(robot_id)
        self.sim = simulation or Simulation(kind="arms")
        self.offset = (float(offset[0]), float(offset[1]))
        self.controller = controller or NavigationController()
        self.navigation_map = navigation_map

        self.battery = max(0.0, min(100.0, float(battery)))
        self.battery_drain_per_second = max(0.0, float(battery_drain_per_second))
        self.health_percent = max(0.0, min(100.0, float(health_percent)))
        self.reliability_score = max(0.0, min(100.0, float(reliability_score)))
        self.available = True
        self.unavailable_reason: str | None = None
        self.busy = False
        self.current_task: Any | None = None
        self.current_job: Job | None = None
        self.completed_jobs: list[Job] = []
        self.task_state = TaskState.IDLE
        self.current_target: tuple[float, float] | None = None
        self.arrived = False
        self.yielding = False
        self.yield_reason: str | None = None
        self.resume_target: tuple[float, float] | None = None
        self.route_waypoints: list[tuple[float, float]] = []
        self.navigation_goal: tuple[float, float] | None = None
        self.station_id: str | None = None
        self.station_destination: tuple[float, float] | None = None
        self._pre_yield_task_state = TaskState.IDLE
        self._mode = "idle"
        self._events: list[dict[str, Any]] = []

    @property
    def position(self) -> tuple[float, float]:
        """Current global XY position (local MuJoCo position plus offset)."""

        return (
            float(self.sim.data.qpos[0]) + self.offset[0],
            float(self.sim.data.qpos[1]) + self.offset[1],
        )

    @property
    def yaw(self) -> float:
        """World yaw extracted from the MuJoCo base rotation matrix."""

        rotation = self.sim.data.xmat[self.sim.base].reshape(3, 3)
        return math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))

    @property
    def velocity(self) -> float:
        """Forward velocity measured by the underlying simulation."""

        return float(self.sim.state()[3])

    @property
    def failed(self) -> bool:
        return self.sim.failed is not None

    @property
    def failure_reason(self) -> str | None:
        return self.sim.failed

    @property
    def target(self) -> tuple[float, float] | None:
        """Compatibility alias for ``current_target``."""

        return self.current_target

    @property
    def task(self) -> Any | None:
        """Compatibility alias for ``current_task``."""

        return self.current_task

    @task.setter
    def task(self, value: Any | None) -> None:
        self.current_task = value

    @property
    def distance_to_target(self) -> float | None:
        if self.current_target is None:
            return None
        x, y = self.position
        return math.hypot(self.current_target[0] - x, self.current_target[1] - y)

    @property
    def planned_target(self) -> tuple[float, float] | None:
        if not self.available:
            return None
        return self.resume_target if self.yielding else self.current_target

    @property
    def planned_distance(self) -> float:
        path = self.planned_path
        if len(path) < 2:
            return math.inf
        return sum(math.dist(first, second) for first, second in zip(path, path[1:]))

    @property
    def planned_path(self) -> tuple[tuple[float, float], ...]:
        target = self.planned_target
        if target is None:
            return ()
        remaining = list(self.route_waypoints)
        if remaining and math.dist(remaining[0], target) <= 1e-9:
            remaining.pop(0)
        return (self.position, target, *remaining)

    def _start_route(self, destination: tuple[float, float]) -> None:
        goal = (float(destination[0]), float(destination[1]))
        points = (
            self.navigation_map.route(self.position, goal)
            if self.navigation_map is not None
            else [goal]
        )
        self.navigation_goal = goal
        self.current_target = points[0] if points else goal
        self.route_waypoints = list(points[1:])
        self.arrived = False
        self._mode = "navigate"

    def _advance_route_if_needed(self) -> bool:
        """Advance to the next aisle waypoint; return True at the final goal."""

        if not self.arrived or self._mode != "navigate":
            return False
        if self.route_waypoints:
            self.current_target = self.route_waypoints.pop(0)
            self.arrived = False
            return False
        return True

    def set_target(self, x: float, y: float) -> None:
        """Start autonomous navigation to a global warehouse coordinate."""

        if self.current_job is not None:
            raise RuntimeError("cannot replace the target of an active job")
        if not self.available:
            raise RuntimeError(f"unavailable robot {self.id} cannot navigate")
        self._start_route((x, y))
        self.busy = True

    def stop(self) -> None:
        """Stop commanding motion and cancel the active navigation target."""

        self.sim.command[:] = [0.0, 0.0]
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = None
        self.arrived = False
        self.busy = self.current_job is not None
        self._mode = "idle"

    def hold_position(self) -> None:
        """Actively return to the current point if simulation drift occurs."""

        if self.current_job is not None:
            raise RuntimeError("cannot hold position while executing a job")
        self.current_target = self.position
        self.route_waypoints = []
        self.navigation_goal = self.position
        self.arrived = True
        self.busy = False
        self._mode = "hold"
        self.sim.command[:] = [0.0, 0.0]

    def initialize_parked(self, station_id: str) -> None:
        """Mark a newly-created robot as docked at its initial station."""

        self.station_id = station_id
        self.station_destination = self.position
        self.task_state = TaskState.PARKED
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = self.position
        self.arrived = True
        self.busy = False
        self._mode = "hold"
        self.sim.command[:] = [0.0, 0.0]

    def start_station_return(
        self,
        station_id: str,
        position: tuple[float, float],
    ) -> None:
        """Route an idle robot to a fleet-reserved charging station."""

        if self.current_job is not None or not self.available:
            raise RuntimeError("only an available idle robot may return to a station")
        self.station_id = station_id
        self.station_destination = (float(position[0]), float(position[1]))
        self.task_state = TaskState.RETURNING_TO_STATION
        self._start_route(self.station_destination)
        self.busy = True

    def leave_station(self) -> None:
        self.station_id = None
        self.station_destination = None
        if self.task_state in {TaskState.PARKED, TaskState.RETURNING_TO_STATION}:
            self.task_state = TaskState.IDLE
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = None
        self.arrived = False
        self.busy = False
        self._mode = "idle"

    def _complete_station_return(self, tick: int) -> None:
        if self.task_state is not TaskState.RETURNING_TO_STATION or self.station_id is None:
            return
        if self.station_destination is not None:
            current = self.position
            self.offset = (
                self.offset[0] + self.station_destination[0] - current[0],
                self.offset[1] + self.station_destination[1] - current[1],
            )
        self.task_state = TaskState.PARKED
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = self.station_destination
        self.arrived = True
        self.busy = False
        self._mode = "hold"
        self.sim.command[:] = [0.0, 0.0]
        self._events.append(
            {
                "type": "station_parked",
                "tick": tick,
                "robot_id": self.id,
                "station_id": self.station_id,
                "position": self.position,
            }
        )

    def begin_yield(self, reason: str, tick: int) -> None:
        """Hold the current point while preserving the intended job target."""

        if self.yielding or not self.available or self.current_target is None:
            return
        self.resume_target = self.current_target
        self._pre_yield_task_state = self.task_state
        self.current_target = self.position
        self.arrived = True
        self.yielding = True
        self.yield_reason = reason
        self.task_state = TaskState.YIELDING
        self._mode = "hold"
        self.sim.command[:] = [0.0, 0.0]
        if self.current_job is not None:
            self.current_job.record(tick, "yielding", self.id, reason=reason)

    def resume_from_yield(self, tick: int) -> None:
        if not self.yielding:
            return
        self.current_target = self.resume_target
        self.resume_target = None
        self.yielding = False
        self.yield_reason = None
        self.task_state = self._pre_yield_task_state
        self.arrived = False
        self._mode = "navigate" if self.current_target is not None else "idle"
        if self.current_job is not None:
            self.current_job.record(tick, "resumed", self.id)

    def set_available(self, available: bool, reason: str | None = None) -> None:
        """Set operational availability without destabilizing MuJoCo physics."""

        self.available = bool(available)
        self.unavailable_reason = None if self.available else (reason or "unavailable")
        if self.available:
            self.task_state = TaskState.IDLE
            self.busy = False
            self.current_target = None
            self.route_waypoints = []
            self.navigation_goal = None
            self._mode = "idle"
            return
        self.yielding = False
        self.yield_reason = None
        self.resume_target = None
        self.current_target = self.position
        self.route_waypoints = []
        self.navigation_goal = self.position
        self.arrived = True
        self.task_state = TaskState.FAILED
        self._mode = "hold"
        self.sim.command[:] = [0.0, 0.0]

    def release_job_for_recovery(self) -> Job | None:
        job = self.current_job
        self.current_job = None
        self.current_task = None
        self.busy = False
        self.current_target = self.position if not self.available else None
        self.route_waypoints = []
        self.navigation_goal = self.current_target
        self.task_state = TaskState.FAILED if not self.available else TaskState.IDLE
        self._mode = "hold" if not self.available else "idle"
        return job

    def assign_job(self, job: Job) -> None:
        """Accept one scheduler-assigned job and begin toward its pickup."""

        if self.failed:
            raise RuntimeError(f"failed robot {self.id} cannot accept a job")
        if self.busy or self.current_job is not None:
            raise RuntimeError(f"busy robot {self.id} cannot accept another job")
        if job.status is not JobStatus.ASSIGNED or job.assigned_robot_id != self.id:
            raise ValueError("job must be assigned to this robot before execution")

        self.station_id = None
        self.station_destination = None
        self.current_job = job
        self.current_task = job
        self.task_state = TaskState.TO_PICKUP
        self._start_route(job.pickup)
        self.busy = True

    def pop_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def _emit(self, event_type: str, job: Job, tick: int) -> None:
        self._events.append(
            {
                "type": event_type,
                "tick": tick,
                "robot_id": self.id,
                "job_id": job.job_id,
                "package_id": job.package_id,
            }
        )

    def _prepare_job_phase(self, tick: int) -> None:
        job = self.current_job
        if job is None or self.yielding or not self.available:
            return
        if job.status is JobStatus.ASSIGNED:
            job.status = JobStatus.TO_PICKUP
            job.record(tick, "to_pickup", self.id)
        elif job.status is JobStatus.PICKED_UP:
            job.status = JobStatus.TO_DROPOFF
            self.task_state = TaskState.TO_DROPOFF
            self._start_route(job.dropoff)
            job.record(tick, "to_dropoff", self.id)
            self._emit("delivery_started", job, tick)

    def _complete_job(self, tick: int) -> None:
        job = self.current_job
        if job is None:
            return
        job.status = JobStatus.COMPLETED
        job.completed_tick = tick
        job.record(tick, "delivered", self.id, position=self.position)
        job.record(tick, "completed", self.id)
        self.completed_jobs.append(job)
        self._emit("job_completed", job, tick)
        self.current_job = None
        self.current_task = None
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = None
        self.task_state = TaskState.IDLE
        self.busy = False
        self.arrived = True
        self._mode = "idle"
        self.sim.command[:] = [0.0, 0.0]

    def fail_current_job(self, tick: int) -> None:
        job = self.current_job
        if job is None or job.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            return
        job.status = JobStatus.FAILED
        job.completed_tick = tick
        job.failure_reason = self.failure_reason or "robot failed"
        self.task_state = TaskState.FAILED
        self.busy = False
        self._emit("job_failed", job, tick)
        self.current_job = None
        self.current_task = None
        self.current_target = None
        self.route_waypoints = []
        self.navigation_goal = None
        self._mode = "idle"

    def _advance_job_after_navigation(self, tick: int) -> None:
        job = self.current_job
        if job is None or self.failed or self.yielding or not self.available or not self.arrived:
            return
        if self.task_state is TaskState.TO_PICKUP and job.status is JobStatus.TO_PICKUP:
            job.status = JobStatus.PICKED_UP
            self.task_state = TaskState.PICKED_UP
            self.busy = True
            job.record(tick, "pickup_reached", self.id, position=self.position)
            job.record(tick, "picked_up", self.id)
            self._emit("package_acquired", job, tick)
        elif self.task_state is TaskState.TO_DROPOFF and job.status is JobStatus.TO_DROPOFF:
            self._complete_job(tick)

    def _control_command(self) -> tuple[float, float]:
        if (
            self.failed
            or (not self.available and self._mode != "hold")
            or self.current_target is None
            or self._mode == "idle"
        ):
            return 0.0, 0.0

        tolerance = (
            self.controller.config.hold_tolerance
            if self._mode == "hold"
            else max(self.controller.config.target_tolerance, 0.16)
            if self.task_state is TaskState.RETURNING_TO_STATION
            else self.controller.config.target_tolerance
        )
        velocity, yaw_rate, _distance, arrived = self.controller.command(
            self.position,
            self.yaw,
            self.current_target,
            tolerance=tolerance,
        )
        self.arrived = arrived
        if arrived:
            if self._mode == "navigate" and self.current_job is None:
                self.busy = False
            return 0.0, 0.0
        return velocity, yaw_rate

    def _drain_battery(self, velocity_command: float) -> None:
        if velocity_command <= 0.0 or self.battery <= 0.0:
            return
        timestep = float(getattr(self.sim.model.opt, "timestep", 0.005))
        movement_fraction = min(
            1.0,
            velocity_command / self.controller.config.max_forward_velocity,
        )
        self.battery = max(
            0.0,
            self.battery
            - self.battery_drain_per_second * timestep * movement_fraction,
        )

    def step(self, tick: int | None = None) -> None:
        """Execute one navigation/control and MuJoCo simulation tick."""

        current_tick = 0 if tick is None else tick
        self._prepare_job_phase(current_tick)
        velocity, yaw_rate = self._control_command()
        self.sim.command[:] = [velocity, yaw_rate]
        self.sim.step()
        self._drain_battery(velocity)

        # Refresh arrival state after physics moved the robot this tick.
        if not self.failed and self.current_target is not None:
            tolerance = (
                self.controller.config.hold_tolerance
                if self._mode == "hold"
                else max(self.controller.config.target_tolerance, 0.16)
                if self.task_state is TaskState.RETURNING_TO_STATION
                else self.controller.config.target_tolerance
            )
            self.arrived = (self.distance_to_target or 0.0) <= tolerance
            if self.arrived and self._mode == "navigate" and self.current_job is None:
                self.busy = False
        at_final_goal = self._advance_route_if_needed()
        if at_final_goal:
            if self.current_job is None and self.task_state is TaskState.RETURNING_TO_STATION:
                self._complete_station_return(current_tick)
            else:
                self._advance_job_after_navigation(current_tick)

    def telemetry(self) -> dict[str, Any]:
        """Return a serializable snapshot suitable for logs or a dashboard."""

        x, y = self.position
        return {
            "id": self.id,
            "x": x,
            "y": y,
            "yaw": self.yaw,
            "velocity": self.velocity,
            "target": self.current_target,
            "planned_target": self.planned_target,
            "navigation_goal": self.navigation_goal,
            "route": list(self.planned_path),
            "remaining_waypoints": list(self.route_waypoints),
            "waiting_point": self.position if self.yielding else None,
            "distance_to_target": self.distance_to_target,
            "arrived": self.arrived,
            "failed": self.failed,
            "failure_reason": self.failure_reason,
            "battery": self.battery,
            "health_percent": self.health_percent,
            "reliability_score": self.reliability_score,
            "busy": self.busy,
            "current_task": (
                self.current_task.job_id
                if isinstance(self.current_task, Job)
                else self.current_task
            ),
            "job_id": self.current_job.job_id if self.current_job else None,
            "job_priority": self.current_job.priority if self.current_job else None,
            "task_state": self.task_state.value,
            "completed_jobs": [job.job_id for job in self.completed_jobs],
            "yielding": self.yielding,
            "yield_reason": self.yield_reason,
            "station_id": self.station_id,
            "station_destination": self.station_destination,
            "parked": self.task_state is TaskState.PARKED,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


# Shorter public name while retaining the explicit name used by early prototypes.
Robot = NexusRobot
