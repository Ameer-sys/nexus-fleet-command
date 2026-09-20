"""Parking-station lifecycle and shelf inventory integration tests."""

import json
import unittest

import numpy as np

from nexus import Fleet, Job, JobStatus, NexusRobot, TaskState, TrafficConfig, TrafficManager
from nexus.demo import WarehouseDemo
from nexus.warehouse import WarehouseNavigationMap, create_packages, create_stations


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


def robot(robot_id: str, position: tuple[float, float], navigation_map=None) -> NexusRobot:
    return NexusRobot(
        robot_id,
        simulation=FakeSimulation(position),
        navigation_map=navigation_map,
    )


class StationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.navigation = WarehouseNavigationMap()

    def test_manual_warehouse_starts_with_three_parked_robots(self) -> None:
        demo = WarehouseDemo(mode="manual")
        state = demo.snapshot()
        self.assertEqual(
            [(item["id"], item["state"], item["station_id"]) for item in state["robots"]],
            [
                ("A", "PARKED", "STATION-NW"),
                ("B", "PARKED", "STATION-NE"),
                ("C", "PARKED", "STATION-SW"),
            ],
        )
        self.assertEqual(len(state["warehouse"]["stations"]), 4)
        json.dumps(state)
        demo.close()

    def test_robot_selects_nearest_available_station(self) -> None:
        unit = robot("A", (1.70, 0.15), self.navigation)
        fleet = Fleet([unit], stations=create_stations().values())
        self.assertEqual(fleet.select_station(unit).station_id, "STATION-SE")

    def test_reserved_station_cannot_be_selected_twice(self) -> None:
        first = robot("A", (1.70, 0.15), self.navigation)
        second = robot("B", (1.65, 0.20), self.navigation)
        fleet = Fleet([first, second], stations=create_stations().values())
        reserved = fleet.return_robot_to_station("A")
        alternate = fleet.select_station(second)
        self.assertEqual(reserved.station_id, "STATION-SE")
        self.assertNotEqual(alternate.station_id, reserved.station_id)
        self.assertEqual(reserved.status, "RESERVED")

    def test_parked_robot_releases_station_when_assigned(self) -> None:
        unit = robot("A", (0.08, 1.92), self.navigation)
        fleet = Fleet([unit], stations=create_stations().values())
        fleet.initialize_station_occupancy("A", "STATION-NW")
        job = Job("J", "BOX", (0.82, 1.45), (1.82, 0.98))
        fleet.submit_job(job)
        fleet.dispatch_jobs()
        self.assertEqual(job.assigned_robot_id, "A")
        self.assertIsNone(unit.station_id)
        self.assertEqual(fleet.stations["STATION-NW"].status, "AVAILABLE")

    def test_idle_robot_routes_to_station_and_parks_cleanly(self) -> None:
        unit = robot("A", (1.10, 0.98), self.navigation)
        fleet = Fleet([unit], stations=create_stations().values(), traffic_enabled=False)
        station = fleet.return_robot_to_station("A")
        self.assertEqual(unit.task_state, TaskState.RETURNING_TO_STATION)
        self.assertGreater(len(unit.planned_path), 2)
        for _ in range(20):
            if unit.task_state is TaskState.PARKED:
                break
            target = unit.current_target
            unit.sim.data.qpos[:2] = (
                target[0] - unit.offset[0],
                target[1] - unit.offset[1],
            )
            fleet.step()
        self.assertEqual(unit.task_state, TaskState.PARKED)
        self.assertEqual(unit.position, station.position)
        self.assertEqual(station.occupied_by, "A")
        self.assertFalse(unit.busy)

    def test_loaded_robot_has_priority_over_returning_robot(self) -> None:
        returning = robot("RETURN", (0.0, 0.98))
        returning.start_station_return("STATION-X", (2.0, 0.98))
        loaded = robot("LOADED", (0.82, 0.16))
        job = Job("J", "BOX", (0.82, 1.80), (0.82, 1.90), priority=9)
        job.status = JobStatus.ASSIGNED
        job.assigned_robot_id = loaded.id
        loaded.assign_job(job)
        manager = TrafficManager(
            TrafficConfig(
                safety_radius=0.16,
                nominal_speed=1.0,
                sample_interval_seconds=0.02,
            )
        )
        events = manager.update([returning, loaded], tick=0)
        self.assertTrue(events)
        self.assertEqual(events[0]["right_of_way"], "LOADED")
        self.assertEqual(events[0]["yielding_robot"], "RETURN")
        self.assertTrue(returning.yielding)


class ShelfInventoryTests(unittest.TestCase):
    def test_every_package_has_distinct_storage_and_walkable_pickup(self) -> None:
        navigation = WarehouseNavigationMap()
        packages = create_packages()
        self.assertEqual(len(packages), 16)
        self.assertTrue(all(item.rack_id and item.shelf_slot for item in packages.values()))
        self.assertTrue(all(item.position != item.access_position for item in packages.values()))
        self.assertTrue(all(navigation.point_is_walkable(item.access_position) for item in packages.values()))
        for item in packages.values():
            route = navigation.route((0.18, 0.08), item.access_position)
            self.assertTrue(all(
                navigation.segment_is_walkable(first, second)
                for first, second in zip([(0.18, 0.08), *route], route)
            ))

    def test_delivery_empties_shelf_and_reset_restores_it(self) -> None:
        demo = WarehouseDemo(mode="manual")
        package = demo.packages["GLASS-01"]
        original = package.storage_position
        job = demo.submit_fleet_command("GLASS-01", "PACKING")
        for _ in range(14_000):
            demo.step()
            if job.status is JobStatus.COMPLETED:
                break
        delivered = next(item for item in demo.snapshot()["packages"] if item["package_id"] == "GLASS-01")
        self.assertEqual(delivered["status"], "DELIVERED")
        self.assertFalse(delivered["on_shelf"])
        self.assertNotEqual(tuple(delivered["position"]), original)
        demo.reset_warehouse()
        restored = demo.packages["GLASS-01"]
        self.assertEqual(restored.position, original)
        self.assertTrue(restored.telemetry()["on_shelf"])
        demo.close()


if __name__ == "__main__":
    unittest.main()
