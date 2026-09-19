"""Run three independent Bracket Bots in one logical NEXUS warehouse."""

from pathlib import Path
import sys

# Support the requested ``python scripts/demo_fleet_navigation.py`` invocation
# without requiring an editable package install first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus import Fleet, NexusRobot


MAX_STEPS = 5_000
TELEMETRY_INTERVAL = 400


def print_telemetry(fleet: Fleet, step: int) -> None:
    print(f"\n--- NEXUS FLEET | STEP {step} ---")
    for robot_id, item in fleet.telemetry().items():
        distance = item["distance_to_target"]
        distance_text = "n/a" if distance is None else f"{distance:.3f}"
        print(
            f"{robot_id}  position=({item['x']:.3f}, {item['y']:.3f}) "
            f"yaw={item['yaw']:+.3f} velocity={item['velocity']:+.3f} "
            f"distance={distance_text} battery={item['battery']:.3f}% "
            f"arrived={item['arrived']} failed={item['failed']}"
        )


def main() -> int:
    fleet = Fleet(
        [
            NexusRobot("A", offset=(0.0, 0.0)),
            NexusRobot("B", offset=(2.0, 0.0)),
            NexusRobot("C", offset=(0.0, 2.0)),
        ]
    )

    fleet["A"].set_target(0.8, 0.5)
    fleet["B"].set_target(1.2, 1.0)
    fleet["C"].set_target(0.7, 1.2)

    for step in range(MAX_STEPS):
        fleet.step()
        if step % TELEMETRY_INTERVAL == 0:
            print_telemetry(fleet, step)
        if fleet.failed or fleet.all_arrived:
            break

    print("\nNEXUS FLEET RESULT\n")
    for robot in fleet:
        item = robot.telemetry()
        if item["failed"]:
            status = f"FAILED ({item['failure_reason']})"
        elif item["arrived"]:
            status = "ARRIVED"
        else:
            status = "TIMED OUT"
        print(
            f"{robot.id}  {status:<10} position=({item['x']:.3f}, {item['y']:.3f}) "
            f"target={item['target']} distance={item['distance_to_target']:.3f}"
        )

    print(f"\nSimulation steps: {fleet.steps}")
    return 0 if fleet.all_arrived and not fleet.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
