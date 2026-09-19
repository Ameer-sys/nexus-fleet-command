"""Small, simulator-independent point-to-point navigation controller."""

from __future__ import annotations

from dataclasses import dataclass
import math


def normalize_angle(angle: float) -> float:
    """Return *angle* wrapped to the closed range [-pi, pi]."""

    wrapped = (angle + math.pi) % (2.0 * math.pi) - math.pi
    # Keep positive pi positive so both endpoints are representable.
    if wrapped == -math.pi and angle > 0.0:
        return math.pi
    return wrapped


@dataclass(frozen=True)
class NavigationConfig:
    """Tunable limits and gains for the proportional controller."""

    target_tolerance: float = 0.10
    hold_tolerance: float = 0.02
    max_forward_velocity: float = 0.18
    max_yaw_rate: float = 0.8
    distance_kp: float = 0.40
    heading_kp: float = 1.8
    minimum_heading_factor: float = 0.10


class NavigationController:
    """Generate safe forward/yaw commands for a target in the XY plane."""

    def __init__(self, config: NavigationConfig | None = None) -> None:
        self.config = config or NavigationConfig()

    def command(
        self,
        position: tuple[float, float],
        yaw: float,
        target: tuple[float, float],
        *,
        tolerance: float | None = None,
    ) -> tuple[float, float, float, bool]:
        """Return ``(velocity, yaw_rate, distance, arrived)``."""

        dx = target[0] - position[0]
        dy = target[1] - position[1]
        distance = math.hypot(dx, dy)
        arrival_radius = self.config.target_tolerance if tolerance is None else tolerance

        if distance <= arrival_radius:
            return 0.0, 0.0, distance, True

        desired_heading = math.atan2(dy, dx)
        heading_error = normalize_angle(desired_heading - yaw)
        yaw_rate = max(
            -self.config.max_yaw_rate,
            min(self.config.max_yaw_rate, self.config.heading_kp * heading_error),
        )

        # The distance term slows the robot near its destination.  The cosine
        # term makes it creep while turning and accelerate once it faces the
        # target, without ever commanding reverse motion.
        distance_speed = min(
            self.config.max_forward_velocity,
            self.config.distance_kp * distance,
        )
        heading_factor = max(
            self.config.minimum_heading_factor,
            math.cos(abs(heading_error)),
        )
        velocity = max(
            0.0,
            min(self.config.max_forward_velocity, distance_speed * heading_factor),
        )
        return velocity, yaw_rate, distance, False
