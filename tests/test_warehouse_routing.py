"""Aisle routing and warehouse-map agreement tests."""

import json
import math
import unittest

import numpy as np

from nexus import (
    JobStatus,
    NexusRobot,
    TrafficConfig,
    WarehouseNavigationMap,
    build_trajectory,
    predict_conflict,
)
from nexus.demo import WarehouseDemo
from nexus.warehouse import create_destinations, create_packages


class _Options:
    timestep = 0.005


class _Model:
    opt = _Options()


class _Data:
    def __init__(self, position=(0.0, 0.0)) -> None:
        self.qpos = np.zeros(7)
        self.qpos[:2] = position
        self.xmat = np.eye(3).reshape(1, 9)


class FakeSimulation:
    def __init__(self, position=(0.0, 0.0)) -> None:
        self.data = _Data(position)
        self.model = _Model()
        self.base = 0
        self.command = np.zeros(2)
        self.failed = None

    def state(self):
        return 0.0, 0.0, np.zeros(3), float(self.command[0])

    def step(self) -> None:
        return None


class WarehouseRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.warehouse = WarehouseNavigationMap()

    def test_pathfinder_returns_aisle_based_route(self) -> None:
        route = self.warehouse.route((0.0, 0.0), (1.82, 1.92))
        self.assertGreaterEqual(len(route), 4)
        internal = route[1:]
        self.assertTrue(
            all(
                math.isclose(first[0], second[0])
                or math.isclose(first[1], second[1])
                for first, second in zip(internal, internal[1:])
            )
        )

    def test_path_does_not_cross_non_walkable_racks(self) -> None:
        route = [(0.0, 0.0), *self.warehouse.route((0.0, 0.0), (1.82, 1.92))]
        self.assertTrue(
            all(
                self.warehouse.segment_is_walkable(first, second)
                for first, second in zip(route, route[1:])
            )
        )

    def test_packages_and_destinations_use_access_waypoints(self) -> None:
        packages = create_packages()
        destinations = create_destinations()
        self.assertNotEqual(packages["MED-KIT-01"].position, packages["MED-KIT-01"].access_position)
        self.assertEqual(packages["MED-KIT-01"].access_position, (0.82, 1.92))
        self.assertEqual(destinations["OUTBOUND"].approach_position, (1.82, 0.98))
        self.assertTrue(self.warehouse.point_is_walkable(packages["MED-KIT-01"].access_position))
        self.assertTrue(self.warehouse.point_is_walkable(destinations["OUTBOUND"].approach_position))

    def test_robot_trajectory_contains_remaining_waypoints(self) -> None:
        robot = NexusRobot("A", simulation=FakeSimulation(), navigation_map=self.warehouse)
        robot.set_target(1.82, 1.92)
        trajectory = build_trajectory(robot, TrafficConfig(nominal_speed=1.0))
        self.assertIsNotNone(trajectory)
        self.assertGreater(len(trajectory.estimated_path), 3)
        self.assertEqual(trajectory.estimated_path, robot.planned_path)

    def test_intersection_conflict_uses_orthogonal_routes(self) -> None:
        horizontal = NexusRobot("A", simulation=FakeSimulation((0.18, 0.98)))
        vertical = NexusRobot("B", simulation=FakeSimulation((0.82, 0.34)))
        horizontal.set_target(1.46, 0.98)
        vertical.set_target(0.82, 1.62)
        config = TrafficConfig(nominal_speed=1.0, safety_radius=0.16, sample_interval_seconds=0.02)
        conflict = predict_conflict(
            build_trajectory(horizontal, config),
            build_trajectory(vertical, config),
            config,
        )
        self.assertIsNotNone(conflict)
        self.assertAlmostEqual(conflict.conflict_point[0], 0.82, places=1)
        self.assertAlmostEqual(conflict.conflict_point[1], 0.98, places=1)

    def test_manual_job_and_recovery_routes_remain_on_graph(self) -> None:
        demo = WarehouseDemo(mode="manual")
        job = demo.submit_fleet_command("HV-001", "SECURE_VAULT")
        robot = demo.fleet.robots[job.assigned_robot_id]
        self.assertEqual(job.pickup, demo.packages["HV-001"].access_position)
        self.assertEqual(job.dropoff, demo.destinations["SECURE_VAULT"].approach_position)
        self.assertTrue(all(
            demo.navigation_map.segment_is_walkable(first, second)
            for first, second in zip(robot.planned_path, robot.planned_path[1:])
        ))
        robot.sim.data.qpos[:2] = (
            job.pickup[0] - robot.offset[0],
            job.pickup[1] - robot.offset[1],
        )
        robot.route_waypoints = []
        robot.current_target = job.pickup
        robot.arrived = True
        robot._prepare_job_phase(demo.fleet.steps)
        robot._advance_job_after_navigation(demo.fleet.steps)
        self.assertEqual(job.status, JobStatus.PICKED_UP)
        demo.fleet.inject_operational_failure(robot.id, "aisle obstruction")
        recovery = demo.fleet.robots[job.assigned_robot_id]
        self.assertTrue(all(
            demo.navigation_map.segment_is_walkable(first, second)
            for first, second in zip(recovery.planned_path, recovery.planned_path[1:])
        ))
        json.dumps(demo.snapshot())
        demo.close()

    def test_reset_and_scripted_demo_keep_navigation_map(self) -> None:
        demo = WarehouseDemo(mode="manual")
        demo.submit_fleet_batch([
            ("BOX-101", "OUTBOUND"),
            ("FR-301", "INBOUND"),
            ("PART-401", "STORAGE_A"),
        ])
        self.assertEqual(sum(robot.busy for robot in demo.fleet), 3)
        demo.reset_warehouse()
        self.assertFalse(demo.fleet.jobs)
        self.assertIn("navigation", demo.snapshot()["warehouse"])
        demo.start()
        self.assertEqual(demo.mode, "scripted")
        self.assertEqual(len(demo.fleet.jobs), 3)
        json.dumps(demo.snapshot())
        demo.close()


if __name__ == "__main__":
    unittest.main()
