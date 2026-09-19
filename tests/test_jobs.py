"""Fleet assignment and pickup-to-delivery state machine tests."""

import math
import unittest

import numpy as np

from nexus import Fleet, Job, JobStatus, NexusRobot, TaskState


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

    def state(self):
        return 0.0, 0.0, np.zeros(3), float(self.command[0])

    def step(self) -> None:
        yaw = math.atan2(self.data.xmat[0, 3], self.data.xmat[0, 0])
        yaw += float(self.command[1]) * self.model.opt.timestep
        cosine, sine = math.cos(yaw), math.sin(yaw)
        self.data.xmat[0] = [cosine, -sine, 0.0, sine, cosine, 0.0, 0.0, 0.0, 1.0]
        self.data.qpos[0] += float(self.command[0]) * cosine * self.model.opt.timestep
        self.data.qpos[1] += float(self.command[0]) * sine * self.model.opt.timestep


def robot(robot_id: str, offset=(0.0, 0.0)) -> NexusRobot:
    return NexusRobot(robot_id, offset=offset, simulation=FakeSimulation())


class JobLifecycleTests(unittest.TestCase):
    def test_assigned_robot_becomes_busy_and_job_has_one_owner(self) -> None:
        fleet = Fleet([robot("A"), robot("B", offset=(3.0, 0.0))])
        job = Job("J1", "P1", pickup=(0.2, 0.0), dropoff=(1.0, 0.0))
        fleet.submit_job(job)
        fleet.dispatch_jobs()

        self.assertEqual(job.status, JobStatus.ASSIGNED)
        self.assertEqual(job.assigned_robot_id, "A")
        self.assertTrue(fleet["A"].busy)
        self.assertFalse(fleet["B"].busy)
        self.assertEqual(sum(item.current_job is job for item in fleet), 1)

    def test_second_job_stays_pending_when_only_robot_is_busy(self) -> None:
        fleet = Fleet([robot("A")])
        first = Job("HIGH", "P1", (0.2, 0.0), (1.0, 0.0), priority=10)
        second = Job("LOW", "P2", (0.3, 0.0), (1.0, 0.0), priority=2)
        fleet.submit_job(second)
        fleet.submit_job(first)
        fleet.dispatch_jobs()

        self.assertEqual(first.status, JobStatus.ASSIGNED)
        self.assertEqual(second.status, JobStatus.PENDING)

    def test_job_progresses_once_through_pickup_and_dropoff(self) -> None:
        fleet = Fleet([robot("A")])
        job = Job("J1", "BOX", pickup=(0.25, 0.0), dropoff=(0.5, 0.0))
        fleet.submit_job(job)
        fleet.dispatch_jobs()
        self.assertEqual(job.status, JobStatus.ASSIGNED)

        fleet.step()
        self.assertEqual(job.status, JobStatus.TO_PICKUP)
        self.assertEqual(fleet["A"].task_state, TaskState.TO_PICKUP)

        fleet["A"].sim.data.qpos[:2] = job.pickup
        fleet.step()
        self.assertEqual(job.status, JobStatus.PICKED_UP)
        self.assertEqual(fleet["A"].task_state, TaskState.PICKED_UP)

        fleet.step()
        self.assertEqual(job.status, JobStatus.TO_DROPOFF)
        self.assertEqual(fleet["A"].task_state, TaskState.TO_DROPOFF)

        fleet["A"].sim.data.qpos[:2] = job.dropoff
        fleet.step()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertFalse(fleet["A"].busy)
        self.assertIsNone(fleet["A"].current_job)
        self.assertEqual(fleet["A"].completed_jobs, [job])
        self.assertEqual(fleet.completed_jobs, [job])

        event_types = [event["type"] for event in fleet.pop_events()]
        self.assertEqual(event_types.count("package_acquired"), 1)
        self.assertEqual(event_types.count("job_completed"), 1)

    def test_job_and_robot_telemetry_is_structured(self) -> None:
        fleet = Fleet([robot("A")])
        job = Job("J1", "BOX", (0.2, 0.0), (0.5, 0.0), priority=8)
        fleet.submit_job(job)
        fleet.dispatch_jobs()
        snapshot = fleet.snapshot()

        self.assertEqual(snapshot["robots"]["A"]["job_id"], "J1")
        self.assertEqual(snapshot["robots"]["A"]["task_state"], "TO_PICKUP")
        self.assertEqual(snapshot["jobs"]["J1"]["priority"], 8)
        self.assertEqual(snapshot["auctions"][0]["winner"], "A")


if __name__ == "__main__":
    unittest.main()
