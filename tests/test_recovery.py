"""Operational failure and automatic job handoff tests."""

import json
import math
import unittest

import numpy as np

from nexus import Auctioneer, Fleet, Job, JobStatus, NexusRobot


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

    def step(self) -> None:
        yaw = math.atan2(self.data.xmat[0, 3], self.data.xmat[0, 0])
        yaw += float(self.command[1]) * self.model.opt.timestep
        cosine, sine = math.cos(yaw), math.sin(yaw)
        self.data.xmat[0] = [cosine, -sine, 0.0, sine, cosine, 0.0, 0.0, 0.0, 1.0]
        self.data.qpos[0] += float(self.command[0]) * cosine * self.model.opt.timestep
        self.data.qpos[1] += float(self.command[0]) * sine * self.model.opt.timestep


def make_robot(robot_id, offset=(0.0, 0.0)):
    return NexusRobot(robot_id, offset=offset, simulation=FakeSimulation())


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = make_robot("A")
        self.second = make_robot("B", offset=(2.0, 0.0))
        self.fleet = Fleet([self.first, self.second], traffic_enabled=False)
        self.job = Job("J", "PACKAGE", pickup=(0.25, 0.0), dropoff=(1.0, 0.0))
        self.fleet.submit_job(self.job)
        self.fleet.dispatch_jobs()
        self.assertEqual(self.job.assigned_robot_id, "A")

    def test_unavailable_robot_is_ineligible_and_job_is_reassigned(self) -> None:
        self.fleet.inject_operational_failure("A", "blocked")

        self.assertFalse(self.first.available)
        self.assertFalse(Auctioneer().bid(self.first, self.job).eligible)
        self.assertEqual(self.job.status, JobStatus.ASSIGNED)
        self.assertEqual(self.job.assigned_robot_id, "B")
        self.assertEqual(self.job.reassignment_count, 1)
        self.assertEqual(self.job.assignment_history, ["A", "B"])
        self.assertIsNone(self.first.current_job)
        self.assertIs(self.second.current_job, self.job)
        self.assertEqual(sum(robot.current_job is self.job for robot in self.fleet), 1)

    def test_package_recovery_point_updates_after_pickup(self) -> None:
        self.fleet.step()
        self.first.sim.data.qpos[:2] = self.job.pickup
        self.fleet.step()
        self.assertEqual(self.job.status, JobStatus.PICKED_UP)
        recovery_point = self.first.position

        self.fleet.inject_operational_failure("A", "blocked after pickup")

        self.assertEqual(self.job.pickup, recovery_point)
        recovery_events = [
            event for event in self.job.history if event.event == "package_recovery_point"
        ]
        self.assertEqual(len(recovery_events), 1)
        self.assertEqual(recovery_events[0].details["position"], recovery_point)

    def test_reassigned_job_completes_exactly_once(self) -> None:
        self.fleet.step()
        self.first.sim.data.qpos[:2] = self.job.pickup
        self.fleet.step()
        self.fleet.inject_operational_failure("A", "blocked after pickup")

        self.fleet.step()
        pickup = self.job.pickup
        self.second.sim.data.qpos[:2] = (
            pickup[0] - self.second.offset[0],
            pickup[1] - self.second.offset[1],
        )
        self.fleet.step()
        self.fleet.step()
        dropoff = self.job.dropoff
        self.second.sim.data.qpos[:2] = (
            dropoff[0] - self.second.offset[0],
            dropoff[1] - self.second.offset[1],
        )
        self.fleet.step()

        self.assertEqual(self.job.status, JobStatus.COMPLETED)
        self.assertEqual(self.fleet.completed_jobs, [self.job])
        completed = [event for event in self.job.history if event.event == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(sum(robot.current_job is self.job for robot in self.fleet), 0)
        json.dumps(self.fleet.snapshot())


if __name__ == "__main__":
    unittest.main()
