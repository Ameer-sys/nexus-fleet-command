"""Interactive Fleet Command behavior and explainable dispatch tests."""

import json
import unittest

from fastapi.testclient import TestClient

from nexus.auction import Auctioneer
from nexus.demo import WarehouseDemo
from nexus.jobs import Job, JobStatus
from nexus.server import ControlCenterRuntime, create_app


class StubRobot:
    def __init__(
        self,
        robot_id: str,
        position: tuple[float, float],
        *,
        health: float,
        reliability: float,
        battery: float = 100.0,
    ) -> None:
        self.id = robot_id
        self.position = position
        self.health_percent = health
        self.reliability_score = reliability
        self.battery = battery
        self.busy = False
        self.failed = False
        self.available = True
        self.yielding = False


class ExplainableAuctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.near = StubRobot("NEAR", (0.9, 0.0), health=65, reliability=70)
        self.healthy = StubRobot("HEALTHY", (0.0, 0.0), health=99, reliability=99)
        self.auctioneer = Auctioneer()

    def test_standard_cargo_normally_favors_distance(self) -> None:
        job = Job("J-STD", "BOX", (1.0, 0.0), (2.0, 0.0), handling_category="STANDARD")
        result = self.auctioneer.run(job, [self.healthy, self.near], tick=0)
        self.assertEqual(result.winner_id, "NEAR")

    def test_high_value_cargo_can_prefer_health_over_distance(self) -> None:
        job = Job("J-HV", "HV", (1.0, 0.0), (2.0, 0.0), handling_category="HIGH_VALUE")
        result = self.auctioneer.run(job, [self.healthy, self.near], tick=0)
        self.assertEqual(result.winner_id, "HEALTHY")
        telemetry = result.telemetry()
        self.assertIn("health", telemetry["bids"][0]["components"])
        json.dumps(telemetry)


class FleetCommandTests(unittest.TestCase):
    def test_richer_inventory_is_selectable_delivered_and_reset_deterministically(self) -> None:
        demo = WarehouseDemo(mode="manual")
        initial = demo.snapshot()
        packages = initial["packages"]
        self.assertEqual(len(packages), 16)
        self.assertEqual(len({item["package_id"] for item in packages}), 16)
        self.assertEqual(
            {item["category"] for item in packages},
            {"STANDARD", "FRAGILE", "HIGH_VALUE", "MEDICAL"},
        )
        original_positions = {item["package_id"]: item["position"] for item in packages}
        job = demo.submit_fleet_command("GLASS-01", "PACKING")
        for _ in range(12_000):
            demo.step()
            if job.status is JobStatus.COMPLETED:
                break
        self.assertEqual(job.status, JobStatus.COMPLETED)
        delivered = next(
            item for item in demo.snapshot()["packages"] if item["package_id"] == "GLASS-01"
        )
        self.assertEqual(delivered["status"], "DELIVERED")
        self.assertEqual(delivered["position"], [1.47, 0.36])
        demo.reset_warehouse()
        reset = demo.snapshot()["packages"]
        self.assertTrue(all(item["status"] == "AVAILABLE" for item in reset))
        self.assertEqual(
            {item["package_id"]: item["position"] for item in reset}, original_positions
        )
        demo.close()

    def test_api_submits_real_manual_job_and_explanation(self) -> None:
        runtime = ControlCenterRuntime(demo_speed=0.1)
        app = create_app(runtime)
        with TestClient(app) as client:
            initial = client.get("/api/snapshot").json()
            self.assertEqual(initial["simulation"]["mode"], "manual")
            self.assertEqual(initial["jobs"], [])
            response = client.post(
                "/api/commands/dispatch",
                json={
                    "package_id": "HV-001",
                    "destination_id": "SECURE_VAULT",
                    "category": "HIGH_VALUE",
                    "priority": 9,
                },
            )
            self.assertEqual(response.status_code, 200)
            state = response.json()
            self.assertEqual(state["jobs"][0]["package_id"], "HV-001")
            self.assertEqual(state["decision"]["winner"], "A")
            self.assertEqual(state["decision"]["category"], "HIGH_VALUE")
            self.assertTrue(state["jobs"][0]["manual_command"])
            json.dumps(state["decision"])

    def test_reset_restores_idle_manual_warehouse(self) -> None:
        demo = WarehouseDemo(mode="manual")
        demo.submit_fleet_command("HV-001", "SECURE_VAULT", category="HIGH_VALUE", priority=9)
        self.assertTrue(demo.fleet.jobs)
        demo.reset_warehouse()
        state = demo.snapshot()
        self.assertEqual(state["simulation"]["mode"], "manual")
        self.assertEqual(state["jobs"], [])
        self.assertFalse(state["simulation"]["running"])
        self.assertTrue(all(item["status"] == "AVAILABLE" for item in state["packages"]))
        demo.close()

    @staticmethod
    def _three_busy() -> tuple[WarehouseDemo, list[Job]]:
        demo = WarehouseDemo(mode="manual")
        jobs = [
            demo.submit_fleet_command("BOX-101", "OUTBOUND", category="STANDARD", priority=3),
            demo.submit_fleet_command("FR-301", "INBOUND", category="STANDARD", priority=6),
            demo.submit_fleet_command("PART-401", "STORAGE_A", category="STANDARD", priority=5),
        ]
        return demo, jobs

    def test_multiple_manual_jobs_coexist_and_three_robots_work(self) -> None:
        demo, jobs = self._three_busy()
        self.assertEqual(len(demo.fleet.jobs), 3)
        self.assertEqual({job.assigned_robot_id for job in jobs}, {"A", "B", "C"})
        self.assertEqual(sum(robot.busy for robot in demo.fleet), 3)
        demo.close()

    def test_package_cannot_have_duplicate_active_job(self) -> None:
        demo = WarehouseDemo(mode="manual")
        demo.submit_fleet_command("BOX-101", "OUTBOUND")
        with self.assertRaisesRegex(ValueError, "already"):
            demo.submit_fleet_command("BOX-101", "INBOUND")
        demo.close()

    def test_fourth_job_queues_then_assigns_when_robot_is_free(self) -> None:
        demo, jobs = self._three_busy()
        queued = demo.submit_fleet_command("MED-KIT-01", "MEDICAL", priority=8)
        self.assertEqual(queued.status, JobStatus.PENDING)
        self.assertIsNone(queued.assigned_robot_id)
        self.assertEqual(demo.last_decision["type"], "QUEUED")
        robot = demo.fleet.robots[jobs[0].assigned_robot_id]
        robot._complete_job(demo.fleet.steps)
        demo.step()
        self.assertIsNotNone(queued.assigned_robot_id)
        self.assertNotEqual(queued.status, JobStatus.PENDING)
        demo.close()

    def test_higher_priority_pending_job_dispatches_first(self) -> None:
        demo, jobs = self._three_busy()
        low = demo.submit_fleet_command("ELECTRONICS-01", "STORAGE_B", priority=2)
        high = demo.submit_fleet_command("MED-KIT-01", "MEDICAL", priority=10)
        robot = demo.fleet.robots[jobs[0].assigned_robot_id]
        robot._complete_job(demo.fleet.steps)
        demo.step()
        self.assertIsNotNone(high.assigned_robot_id)
        self.assertEqual(low.status, JobStatus.PENDING)
        demo.close()

    def test_failure_does_not_corrupt_unrelated_jobs_and_recovery_can_wait(self) -> None:
        demo, jobs = self._three_busy()
        failed_job = jobs[1]
        for _ in range(6_000):
            demo.step()
            if failed_job.status is JobStatus.TO_DROPOFF:
                break
        self.assertEqual(failed_job.status, JobStatus.TO_DROPOFF)
        unrelated = {
            job.job_id: job.assigned_robot_id for job in jobs if job is not failed_job
        }
        failed_robot = failed_job.assigned_robot_id
        self.assertTrue(demo.inject_failure(failed_robot, manual=True))
        if all(demo.fleet.robots[robot_id].busy for robot_id in unrelated.values()):
            self.assertEqual(failed_job.status, JobStatus.PENDING)
            self.assertIsNone(failed_job.assigned_robot_id)
        for job_id, robot_id in unrelated.items():
            self.assertEqual(demo.fleet.robots[robot_id].current_job.job_id, job_id)
        for _ in range(14_000):
            demo.step()
            if failed_job.status is JobStatus.COMPLETED:
                break
        self.assertEqual(failed_job.status, JobStatus.COMPLETED)
        self.assertEqual(failed_job.reassignment_count, 1)
        demo.close()

    def test_manual_failure_recovers_and_completes(self) -> None:
        demo = WarehouseDemo(mode="manual")
        job = demo.submit_fleet_command(
            "HV-001", "SECURE_VAULT", category="HIGH_VALUE", priority=9
        )
        first_robot = job.assigned_robot_id
        self.assertEqual(first_robot, "A")
        for _ in range(8_000):
            demo.step()
            if job.status is JobStatus.TO_DROPOFF:
                break
        self.assertEqual(job.status, JobStatus.TO_DROPOFF)
        self.assertTrue(demo.inject_failure(first_robot, manual=True))
        self.assertNotEqual(job.assigned_robot_id, first_robot)
        self.assertEqual(demo.last_decision["type"], "RECOVERY_DECISION")
        for _ in range(12_000):
            demo.step()
            if demo.finished:
                break
        self.assertTrue(demo.finished)
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.reassignment_count, 1)
        package = next(item for item in demo.snapshot()["packages"] if item["package_id"] == "HV-001")
        self.assertEqual(package["status"], "DELIVERED")
        demo.close()

    def test_scripted_demo_entry_remains_available(self) -> None:
        demo = WarehouseDemo(mode="manual")
        demo.start()
        self.assertEqual(demo.mode, "scripted")
        self.assertTrue(demo.running)
        self.assertEqual(len(demo.fleet.jobs), 3)
        demo.close()


class BatchFleetCommandTests(unittest.TestCase):
    def test_batch_api_creates_three_real_jobs_and_assigns_three_robots(self) -> None:
        runtime = ControlCenterRuntime(demo_speed=0.1)
        app = create_app(runtime)
        with TestClient(app) as client:
            response = client.post(
                "/api/jobs/batch",
                json={
                    "items": [
                        {"package_id": "MED-KIT-01", "destination": "OUTBOUND"},
                        {"package_id": "ELECTRONICS-01", "destination": "OUTBOUND"},
                        {"package_id": "BOX-003", "destination": "OUTBOUND"},
                    ]
                },
            )
            self.assertEqual(response.status_code, 200)
            state = response.json()
            self.assertEqual(state["batch"]["submitted"], 3)
            self.assertEqual(state["batch"]["active"], 3)
            self.assertEqual(state["batch"]["waiting"], 0)
            self.assertEqual(len(state["jobs"]), 3)
            self.assertEqual(
                {job["assigned_robot"] for job in state["jobs"]},
                {"A", "B", "C"},
            )
            self.assertEqual(len(runtime.demo.fleet.auction_history), 3)

    def test_five_job_batch_assigns_three_and_queues_two_by_priority(self) -> None:
        demo = WarehouseDemo(mode="manual")
        jobs = demo.submit_fleet_batch(
            [
                ("BOX-001", "OUTBOUND"),
                ("BOX-002", "OUTBOUND"),
                ("BOX-003", "OUTBOUND"),
                ("ELECTRONICS-01", "OUTBOUND"),
                ("MED-KIT-01", "OUTBOUND"),
            ]
        )
        self.assertEqual(sum(job.status is not JobStatus.PENDING for job in jobs), 3)
        self.assertEqual(sum(job.status is JobStatus.PENDING for job in jobs), 2)
        self.assertEqual([job.package_id for job in demo.fleet.pending_jobs], ["MED-KIT-01", "ELECTRONICS-01"])
        robot = next(robot for robot in demo.fleet if robot.busy)
        robot._complete_job(demo.fleet.steps)
        demo.step()
        high = next(job for job in jobs if job.package_id == "MED-KIT-01")
        low = next(job for job in jobs if job.package_id == "ELECTRONICS-01")
        self.assertIsNotNone(high.assigned_robot_id)
        self.assertEqual(low.status, JobStatus.PENDING)
        demo.close()

    def test_batch_validation_is_atomic_for_duplicates_and_active_packages(self) -> None:
        runtime = ControlCenterRuntime(demo_speed=0.1)
        app = create_app(runtime)
        with TestClient(app) as client:
            duplicate = client.post(
                "/api/jobs/batch",
                json={"items": [
                    {"package_id": "BOX-003", "destination": "OUTBOUND"},
                    {"package_id": "BOX-003", "destination": "INBOUND"},
                ]},
            )
            self.assertEqual(duplicate.status_code, 409)
            self.assertIn("duplicate", duplicate.json()["detail"])
            self.assertEqual(client.get("/api/snapshot").json()["jobs"], [])

        demo = WarehouseDemo(mode="manual")
        self.assertEqual(demo.fleet.jobs, {})
        demo.submit_fleet_command("BOX-003", "OUTBOUND")
        with self.assertRaisesRegex(ValueError, "already"):
            demo.submit_fleet_batch(
                [("MED-KIT-01", "OUTBOUND"), ("BOX-003", "OUTBOUND")]
            )
        self.assertNotIn("MED-KIT-01", {job.package_id for job in demo.fleet.jobs.values()})
        demo.close()

    def test_batch_preserves_each_packages_category_priority_and_risk(self) -> None:
        demo = WarehouseDemo(mode="manual")
        jobs = demo.submit_fleet_batch(
            [
                ("MED-KIT-01", "OUTBOUND"),
                ("ELECTRONICS-01", "OUTBOUND"),
                ("BOX-003", "OUTBOUND"),
            ]
        )
        properties = {
            job.package_id: (job.handling_category, job.priority, job.risk)
            for job in jobs
        }
        self.assertEqual(properties["MED-KIT-01"], ("MEDICAL", 10, "CRITICAL"))
        self.assertEqual(properties["ELECTRONICS-01"], ("FRAGILE", 7, "MEDIUM"))
        self.assertEqual(properties["BOX-003"], ("STANDARD", 3, "LOW"))
        demo.close()

    def test_batch_jobs_use_existing_traffic_coordination(self) -> None:
        demo = WarehouseDemo(mode="manual")
        jobs = demo.submit_fleet_batch(
            [("BOX-101", "OUTBOUND"), ("FR-301", "INBOUND")]
        )
        for _ in range(12_000):
            demo.step()
            if all(job.status is JobStatus.COMPLETED for job in jobs):
                break
        traffic_events = [event for event in demo.events if event.category == "TRAFFIC"]
        self.assertTrue(any("Conflict" in event.message for event in traffic_events))
        self.assertTrue(any("yielding" in event.message for event in traffic_events))
        self.assertTrue(any("resumed" in event.message for event in traffic_events))
        self.assertGreater(demo.fleet.traffic.conflicts_prevented, 0)
        demo.close()


class ManualTrafficIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.demo = WarehouseDemo(mode="manual")
        cls.low = cls.demo.submit_fleet_command(
            "BOX-101", "OUTBOUND", category="STANDARD", priority=3
        )
        cls.high = cls.demo.submit_fleet_command(
            "FR-301", "INBOUND", category="STANDARD", priority=10
        )
        for _ in range(12_000):
            cls.demo.step()
            if cls.demo.finished:
                break
        cls.traffic_events = [
            event.telemetry() for event in cls.demo.events if event.category == "TRAFFIC"
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.demo.close()

    def test_crossing_manual_routes_trigger_real_conflict(self) -> None:
        conflicts = [event for event in self.traffic_events if "Conflict" in event["message"]]
        self.assertTrue(conflicts)
        self.assertGreater(self.demo.fleet.traffic.conflicts_prevented, 0)

    def test_higher_priority_job_receives_right_of_way(self) -> None:
        conflict = next(event for event in self.traffic_events if "Conflict" in event["message"])
        self.assertEqual(conflict["details"]["right_of_way"], self.high.assigned_robot_id)
        self.assertEqual(conflict["details"]["reason"], "higher job priority")

    def test_yielding_robot_resumes_and_both_jobs_complete(self) -> None:
        self.assertTrue(any("yielding" in event["message"] for event in self.traffic_events))
        self.assertTrue(any("resumed" in event["message"] for event in self.traffic_events))
        self.assertEqual(self.low.status, JobStatus.COMPLETED)
        self.assertEqual(self.high.status, JobStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
