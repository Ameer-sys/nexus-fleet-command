"""Auction eligibility, scoring, and winner selection tests."""

import unittest

from nexus import Auctioneer, Job


class StubRobot:
    def __init__(
        self,
        robot_id,
        position,
        *,
        battery=100.0,
        busy=False,
        failed=False,
    ):
        self.id = robot_id
        self.position = position
        self.battery = battery
        self.busy = busy
        self.failed = failed


class AuctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = Job("J", "P", pickup=(1.0, 0.0), dropoff=(2.0, 0.0))
        self.auctioneer = Auctioneer()

    def test_closer_idle_robot_normally_wins(self) -> None:
        near = StubRobot("NEAR", (0.8, 0.0))
        far = StubRobot("FAR", (-1.0, 0.0))
        result = self.auctioneer.run(self.job, [far, near], tick=4)

        self.assertEqual(result.winner_id, "NEAR")
        self.assertAlmostEqual(result.bids[1].total, 0.2)

    def test_failed_robot_cannot_win(self) -> None:
        failed = StubRobot("FAILED", (1.0, 0.0), failed=True)
        healthy = StubRobot("HEALTHY", (0.0, 0.0))
        result = self.auctioneer.run(self.job, [failed, healthy], tick=0)

        self.assertEqual(result.winner_id, "HEALTHY")
        self.assertFalse(result.bids[0].eligible)
        self.assertEqual(result.bids[0].reason, "robot failed")

    def test_critically_low_battery_robot_cannot_win(self) -> None:
        low = StubRobot("LOW", (1.0, 0.0), battery=10.0)
        charged = StubRobot("CHARGED", (0.0, 0.0), battery=90.0)
        result = self.auctioneer.run(self.job, [low, charged], tick=0)

        self.assertEqual(result.winner_id, "CHARGED")
        self.assertEqual(result.bids[0].reason, "battery critically low")

    def test_bid_explains_battery_and_workload_penalties(self) -> None:
        robot = StubRobot("A", (0.0, 0.0), battery=75.0)
        bid = self.auctioneer.bid(robot, self.job)
        self.assertAlmostEqual(bid.distance, 1.0)
        self.assertAlmostEqual(bid.battery_penalty, 0.25)
        self.assertAlmostEqual(bid.total, 1.25)

        busy_bid = self.auctioneer.bid(
            StubRobot("B", (1.0, 0.0), busy=True), self.job
        )
        self.assertFalse(busy_bid.eligible)
        self.assertEqual(busy_bid.workload_penalty, 100.0)


if __name__ == "__main__":
    unittest.main()
