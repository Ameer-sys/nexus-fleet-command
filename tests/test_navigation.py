"""Unit tests for NEXUS navigation, robot state, and fleet stepping."""

import math
import unittest

import numpy as np

from nexus import Fleet, NavigationController, NexusRobot, normalize_angle


class _Options:
    timestep = 0.005


class _Model:
    opt = _Options()


class _Data:
    def __init__(self) -> None:
        self.qpos = np.zeros(7)
        self.xmat = np.eye(3).reshape(1, 9)
        self.time = 0.0


class FakeSimulation:
    def __init__(self) -> None:
        self.data = _Data()
        self.model = _Model()
        self.base = 0
        self.command = np.zeros(2)
        self.failed = None
        self.step_count = 0

    def state(self):
        return 0.0, 0.0, np.zeros(3), float(self.command[0])

    def step(self) -> None:
        self.step_count += 1
        self.data.time += self.model.opt.timestep
        yaw = math.atan2(self.data.xmat[0, 3], self.data.xmat[0, 0])
        yaw += float(self.command[1]) * self.model.opt.timestep
        cosine, sine = math.cos(yaw), math.sin(yaw)
        self.data.xmat[0] = [cosine, -sine, 0.0, sine, cosine, 0.0, 0.0, 0.0, 1.0]
        self.data.qpos[0] += float(self.command[0]) * cosine * self.model.opt.timestep
        self.data.qpos[1] += float(self.command[0]) * sine * self.model.opt.timestep


class NavigationTests(unittest.TestCase):
    def test_normalize_angle(self) -> None:
        self.assertAlmostEqual(normalize_angle(3.0 * math.pi), math.pi)
        self.assertAlmostEqual(normalize_angle(-3.0 * math.pi), -math.pi)
        self.assertAlmostEqual(normalize_angle(0.25), 0.25)

    def test_controller_limits_and_heading_slowdown(self) -> None:
        controller = NavigationController()
        straight = controller.command((0.0, 0.0), 0.0, (1.0, 0.0))
        turning = controller.command((0.0, 0.0), math.pi, (1.0, 0.0))

        self.assertLessEqual(straight[0], 0.18)
        self.assertLessEqual(abs(turning[1]), 0.8)
        self.assertLess(turning[0], straight[0])
        self.assertGreaterEqual(turning[0], 0.0)

    def test_robot_offset_yaw_target_and_stop(self) -> None:
        simulation = FakeSimulation()
        robot = NexusRobot("A", offset=(2.0, 3.0), simulation=simulation)
        self.assertEqual(robot.position, (2.0, 3.0))
        self.assertAlmostEqual(robot.yaw, 0.0)

        robot.set_target(3.0, 3.0)
        robot.step()
        self.assertGreater(simulation.command[0], 0.0)
        self.assertTrue(robot.busy)

        robot.stop()
        self.assertEqual(simulation.command.tolist(), [0.0, 0.0])
        self.assertIsNone(robot.target)
        self.assertFalse(robot.busy)

    def test_hold_position_corrects_drift(self) -> None:
        simulation = FakeSimulation()
        robot = NexusRobot("H", simulation=simulation)
        robot.hold_position()
        simulation.data.qpos[0] = -0.08
        robot.step()

        self.assertGreater(simulation.command[0], 0.0)
        self.assertFalse(robot.arrived)
        self.assertEqual(robot.target, (0.0, 0.0))

    def test_fleet_steps_every_simulation_and_reports_telemetry(self) -> None:
        first_sim, second_sim = FakeSimulation(), FakeSimulation()
        fleet = Fleet(
            [
                NexusRobot("A", simulation=first_sim),
                NexusRobot("B", offset=(2.0, 0.0), simulation=second_sim),
            ]
        )
        fleet["A"].set_target(0.5, 0.0)
        fleet["B"].set_target(1.5, 0.0)
        fleet.step()

        self.assertEqual(first_sim.step_count, 1)
        self.assertEqual(second_sim.step_count, 1)
        self.assertEqual(set(fleet.telemetry()), {"A", "B"})
        self.assertEqual(fleet.steps, 1)


if __name__ == "__main__":
    unittest.main()
