"""Deterministic, explainable robot bidding for warehouse jobs."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any, Iterable

from .jobs import Job

if TYPE_CHECKING:
    from .robot import NexusRobot


@dataclass(frozen=True)
class AuctionConfig:
    critical_battery_percent: float = 10.0
    battery_penalty_weight: float = 1.0
    workload_penalty: float = 100.0
    category_weights: tuple[tuple[str, tuple[float, float, float, float, float]], ...] = (
        ("STANDARD", (1.0, 1.0, 0.0, 0.0, 0.0)),
        ("HIGH_VALUE", (0.55, 3.0, 5.0, 6.0, 1.0)),
        ("MEDICAL", (0.8, 4.0, 4.0, 7.0, 0.7)),
        ("FRAGILE", (0.75, 2.0, 6.0, 4.0, 2.0)),
    )

    def weights_for(self, category: str) -> tuple[float, float, float, float, float]:
        weights = dict(self.category_weights)
        return weights.get(category.upper(), weights["STANDARD"])


@dataclass(frozen=True)
class Bid:
    robot_id: str
    eligible: bool
    distance: float
    battery_penalty: float
    workload_penalty: float
    total: float | None
    reason: str | None = None
    distance_contribution: float = 0.0
    battery_contribution: float = 0.0
    health_percent: float = 100.0
    health_contribution: float = 0.0
    reliability_score: float = 100.0
    reliability_contribution: float = 0.0
    traffic_risk: str = "LOW"
    traffic_contribution: float = 0.0
    category: str = "STANDARD"
    battery_percent: float = 100.0

    def telemetry(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "eligible": self.eligible,
            "distance": self.distance,
            "battery_penalty": self.battery_penalty,
            "workload_penalty": self.workload_penalty,
            "total": self.total,
            "reason": self.reason,
            "components": {
                "distance": {"value": self.distance, "cost": self.distance_contribution},
                "battery": {"value": self.battery_percent, "cost": self.battery_contribution},
                "health": {"value": self.health_percent, "cost": self.health_contribution},
                "reliability": {
                    "value": self.reliability_score,
                    "cost": self.reliability_contribution,
                },
                "traffic": {"value": self.traffic_risk, "cost": self.traffic_contribution},
                "workload": {"value": "BUSY" if self.workload_penalty else "IDLE", "cost": self.workload_penalty},
            },
            "category": self.category,
        }


@dataclass(frozen=True)
class AuctionResult:
    job_id: str
    bids: tuple[Bid, ...]
    winner_id: str | None
    tick: int

    def telemetry(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "winner": self.winner_id,
            "tick": self.tick,
            "bids": [bid.telemetry() for bid in self.bids],
        }


class Auctioneer:
    """Score robots without coupling allocation policy into robot execution."""

    def __init__(self, config: AuctionConfig | None = None) -> None:
        self.config = config or AuctionConfig()

    def bid(self, robot: NexusRobot, job: Job) -> Bid:
        x, y = robot.position
        distance = math.hypot(job.pickup[0] - x, job.pickup[1] - y)
        base_battery_penalty = (
            max(0.0, 100.0 - robot.battery) / 100.0
        ) * self.config.battery_penalty_weight
        category = getattr(job, "handling_category", "STANDARD").upper()
        distance_weight, battery_weight, health_weight, reliability_weight, traffic_weight = (
            self.config.weights_for(category)
        )
        health = float(getattr(robot, "health_percent", 100.0))
        reliability = float(getattr(robot, "reliability_score", 100.0))
        traffic_risk = "HIGH" if getattr(robot, "yielding", False) else "LOW"
        distance_contribution = distance * distance_weight
        battery_penalty = base_battery_penalty * battery_weight
        health_contribution = max(0.0, 100.0 - health) / 100.0 * health_weight
        reliability_contribution = (
            max(0.0, 100.0 - reliability) / 100.0 * reliability_weight
        )
        traffic_contribution = (1.0 if traffic_risk == "HIGH" else 0.0) * traffic_weight
        workload_penalty = self.config.workload_penalty if robot.busy else 0.0

        reason = None
        if robot.failed:
            reason = "robot failed"
        elif not getattr(robot, "available", True):
            reason = "robot unavailable"
        elif robot.battery <= self.config.critical_battery_percent:
            reason = "battery critically low"
        elif robot.busy:
            reason = "robot busy"

        if reason is not None:
            return Bid(
                robot.id, False, distance, battery_penalty, workload_penalty, None, reason,
                distance_contribution, battery_penalty, health, health_contribution,
                reliability, reliability_contribution, traffic_risk, traffic_contribution,
                category, robot.battery,
            )

        total = (
            distance_contribution
            + battery_penalty
            + health_contribution
            + reliability_contribution
            + traffic_contribution
            + workload_penalty
        )
        return Bid(
            robot.id, True, distance, battery_penalty, workload_penalty, total, None,
            distance_contribution, battery_penalty, health, health_contribution,
            reliability, reliability_contribution, traffic_risk, traffic_contribution,
            category, robot.battery,
        )

    def run(self, job: Job, robots: Iterable[NexusRobot], tick: int) -> AuctionResult:
        bids = tuple(self.bid(robot, job) for robot in robots)
        eligible = [bid for bid in bids if bid.eligible and bid.total is not None]
        winner = min(eligible, key=lambda bid: (bid.total, bid.robot_id)) if eligible else None
        return AuctionResult(job.job_id, bids, winner.robot_id if winner else None, tick)

    def has_eligible_robot(self, robots: Iterable[NexusRobot]) -> bool:
        return any(
            not robot.failed
            and getattr(robot, "available", True)
            and not robot.busy
            and robot.battery > self.config.critical_battery_percent
            for robot in robots
        )
