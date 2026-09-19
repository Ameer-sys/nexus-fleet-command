"""Control-center state, reset, API, and serialization tests."""

import json
import unittest

from fastapi.testclient import TestClient

from nexus.demo import WarehouseDemo
from nexus.server import ControlCenterRuntime, create_app


class DashboardStateTests(unittest.TestCase):
    def test_snapshot_is_json_serializable_and_ui_ready(self) -> None:
        demo = WarehouseDemo()
        snapshot = demo.snapshot()
        json.dumps(snapshot)

        self.assertEqual(len(snapshot["robots"]), 3)
        self.assertEqual(len(snapshot["jobs"]), 3)
        self.assertIn("trajectories", snapshot)
        self.assertIn("conflicts", snapshot)
        self.assertIn("events", snapshot)
        self.assertEqual(snapshot["simulation"]["phase"], "READY")
        self.assertEqual(snapshot["stats"]["robots_online"], 3)

    def test_start_produces_assignment_events(self) -> None:
        demo = WarehouseDemo()
        demo.start()
        demo.step()
        snapshot = demo.snapshot()

        self.assertGreater(snapshot["simulation"]["tick"], 0)
        categories = [event["category"] for event in snapshot["events"]]
        self.assertIn("AUCTION", categories)
        self.assertTrue(all(robot["job_id"] for robot in snapshot["robots"]))

    def test_demo_reset_restores_initial_state(self) -> None:
        demo = WarehouseDemo()
        demo.start()
        for _ in range(5):
            demo.step()
        demo.reset()
        snapshot = demo.snapshot()

        self.assertEqual(snapshot["simulation"]["tick"], 0)
        self.assertFalse(snapshot["simulation"]["running"])
        self.assertEqual(len(snapshot["jobs"]), 3)
        self.assertEqual(snapshot["stats"]["jobs_completed"], 0)


class DashboardServerTests(unittest.TestCase):
    def test_server_initializes_and_controls_work(self) -> None:
        runtime = ControlCenterRuntime(demo_speed=0.1)
        app = create_app(runtime)
        with TestClient(app) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("NEXUS", page.text)
            self.assertIn("CONTROL CENTER", page.text)

            initial = client.get("/api/snapshot").json()
            self.assertEqual(len(initial["robots"]), 3)
            self.assertEqual(initial["simulation"]["phase"], "READY")

            started = client.post("/api/demo/start")
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started.json()["simulation"]["running"])

            paused = client.post("/api/demo/pause")
            self.assertEqual(paused.status_code, 200)
            self.assertFalse(paused.json()["simulation"]["running"])

            reset = client.post("/api/demo/reset")
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(reset.json()["simulation"]["tick"], 0)
            self.assertFalse(reset.json()["simulation"]["running"])

            priority = client.post("/api/demo/priority")
            self.assertEqual(priority.status_code, 200)
            self.assertEqual(len(priority.json()["jobs"]), 4)
            self.assertEqual(priority.json()["stats"]["manual_interventions"], 1)

            failure = client.post("/api/demo/failure/C")
            self.assertEqual(failure.status_code, 200)
            robot_c = next(
                robot for robot in failure.json()["robots"] if robot["id"] == "C"
            )
            self.assertFalse(robot_c["available"])
            self.assertEqual(failure.json()["stats"]["manual_interventions"], 2)


if __name__ == "__main__":
    unittest.main()
