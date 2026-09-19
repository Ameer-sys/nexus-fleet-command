"""Trajectory conflict prediction and yield/resume tests."""

import math
import unittest

import numpy as np

from nexus import (
    NexusRobot,
    PlannedTrajectory,
    TrafficConfig,
    TrafficManager,
    choose_right_of_way,
    predict_conflict,
)


def trajectory(robot_id, start, target, speed=1.0, priority=5):
    distance = math.hypot(target[0] - start[0], target[1] - start[1])
    return PlannedTrajectory(
        robot_id,
        start,
        target,
        (start, target),
        distance / speed,
        priority,
        speed,
    )


class _Options:
    timestep = 0.005


class _Model:
    opt = _Options()


class _Data:
    def __init__(self) -> None:
        self.qpos = np.zeros(7)
        self.xmat = np.eye(3).reshape(1, 9)


class FakeSimulation:
    def __init__(self) -> None:
        self.data = _Data()
        self.model = _Model()
        self.base = 0
        self.command = np.zeros(2)
        self.failed = None

    def state(self):
        return 0.0, 0.0, np.zeros(3), float(self.command[0])

    def step(self):
        return None


class _Job:
    def __init__(self, priority):
        self.priority = priority


class _Robot:
    def __init__(self, robot_id, priority, distance):
        self.id = robot_id
        self.current_job = _Job(priority)
        self.planned_distance = distance


class TrafficTests(unittest.TestCase):
    def test_crossing_paths_produce_conflict(self) -> None:
        config = TrafficConfig(safety_radius=0.15, sample_interval_seconds=0.05)
        first = trajectory("A", (0.0, 0.0), (2.0, 0.0))
        second = trajectory("B", (1.0, -1.0), (1.0, 1.0))
        conflict = predict_conflict(first, second, config)

        self.assertIsNotNone(conflict)
        self.assertLess(conflict.predicted_distance, config.safe_separation)
        self.assertAlmostEqual(conflict.conflict_point[0], 1.0, places=1)

    def test_non_crossing_paths_do_not_conflict(self) -> None:
        config = TrafficConfig(safety_radius=0.15)
        first = trajectory("A", (0.0, 0.0), (2.0, 0.0))
        second = trajectory("B", (0.0, 1.0), (2.0, 1.0))
        self.assertIsNone(predict_conflict(first, second, config))

    def test_higher_priority_job_gets_right_of_way(self) -> None:
        high = _Robot("HIGH", priority=10, distance=2.0)
        low = _Robot("LOW", priority=2, distance=0.1)
        winner, loser, reason = choose_right_of_way(high, low)
        self.assertEqual(winner.id, "HIGH")
        self.assertEqual(loser.id, "LOW")
        self.assertEqual(reason, "higher job priority")

    def test_yielding_robot_holds_then_resumes_when_safe(self) -> None:
        first = NexusRobot("A", simulation=FakeSimulation())
        second = NexusRobot("B", offset=(1.0, -1.0), simulation=FakeSimulation())
        first.set_target(2.0, 0.0)
        second.set_target(1.0, 1.0)
        manager = TrafficManager(
            TrafficConfig(
                safety_radius=0.15,
                nominal_speed=1.0,
                sample_interval_seconds=0.05,
                minimum_yield_ticks=10,
                clear_evaluations_to_resume=2,
            )
        )

        events = manager.update([first, second], tick=0)
        self.assertTrue(second.yielding)
        self.assertEqual(events[0]["right_of_way"], "A")
        self.assertEqual(second.task_state.value, "YIELDING")

        first.stop()
        manager.update([first, second], tick=10)
        events = manager.update([first, second], tick=50)
        self.assertFalse(second.yielding)
        self.assertEqual(events[0]["type"], "traffic_resumed")
        self.assertEqual(second.target, (1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
