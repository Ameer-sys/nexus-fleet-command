"""Demonstrate priority scheduling, auctions, and autonomous job execution."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus import Fleet, Job, JobStatus, NexusRobot


MAX_TICKS = 12_000
DYNAMIC_JOB_TICK = 300
TELEMETRY_INTERVAL = 600


def priority_label(priority: int) -> str:
    if priority >= 9:
        return "CRITICAL"
    if priority >= 6:
        return "HIGH"
    if priority >= 3:
        return "NORMAL"
    return "LOW"


def print_job_request(job: Job, *, dynamic: bool = False) -> None:
    if dynamic:
        print("\n!!! NEW PRIORITY REQUEST !!!")
    print("\n=====================================")
    print("NEXUS JOB REQUEST")
    print("=====================================")
    print(job.job_id)
    print(f"Package: {job.package_id}")
    print(f"Priority: {priority_label(job.priority)} / {job.priority}")
    print(f"Pickup: {job.pickup}")
    print(f"Dropoff: {job.dropoff}")


def print_auction(event: dict) -> None:
    print(f"\nAUCTION: {event['job_id']}")
    for bid in event["bids"]:
        if bid["eligible"]:
            print(
                f"Robot {bid['robot_id']}  total={bid['total']:.3f} "
                f"(distance={bid['distance']:.3f} + "
                f"battery={bid['battery_penalty']:.3f} + "
                f"workload={bid['workload_penalty']:.3f})"
            )
        else:
            print(f"Robot {bid['robot_id']}  INELIGIBLE ({bid['reason']})")
    if event["winner"]:
        print(f"WINNER: Robot {event['winner']}")
        print(f"{event['job_id']} -> ROBOT {event['winner']}")
    else:
        print("WINNER: none; job remains pending")


def print_events(events: list[dict]) -> None:
    for event in events:
        event_type = event["type"]
        if event_type == "auction":
            print_auction(event)
        elif event_type == "package_acquired":
            print(f"\nRobot {event['robot_id']} reached {event['package_id']}")
            print("PACKAGE ACQUIRED")
        elif event_type == "delivery_started":
            print(
                f"Robot {event['robot_id']} delivering {event['package_id']} -> dropoff"
            )
        elif event_type == "job_completed":
            print(
                f"JOB {event['job_id']} COMPLETED BY ROBOT {event['robot_id']}"
            )
        elif event_type == "job_failed":
            print(f"JOB {event['job_id']} FAILED WITH ROBOT {event['robot_id']}")


def print_fleet_telemetry(fleet: Fleet) -> None:
    print(f"\n--- NEXUS OPERATIONS | TICK {fleet.steps} ---")
    for robot_id, item in fleet.telemetry().items():
        print(
            f"{robot_id} | {item['job_id'] or '-':<10} | {item['task_state']:<10} "
            f"| position=({item['x']:.3f}, {item['y']:.3f}) "
            f"| battery={item['battery']:.3f}% | failed={item['failed']}"
        )
    pending = [job.job_id for job in fleet.pending_jobs]
    if pending:
        print(f"Pending: {', '.join(pending)}")


def main() -> int:
    fleet = Fleet(
        [
            NexusRobot("A", offset=(0.0, 0.0)),
            NexusRobot("B", offset=(2.0, 0.0)),
            NexusRobot("C", offset=(0.0, 2.0)),
        ]
    )

    initial_jobs = [
        Job(
            "JOB-001",
            "STANDARD-BOX",
            pickup=(0.5, 0.4),
            dropoff=(1.5, 1.5),
            priority=3,
        ),
        Job(
            "JOB-002",
            "MEDICAL-01",
            pickup=(1.8, 0.4),
            dropoff=(0.5, 1.6),
            priority=10,
        ),
        Job(
            "JOB-003",
            "ELECTRONICS-42",
            pickup=(0.4, 1.7),
            dropoff=(1.7, 0.8),
            priority=6,
        ),
    ]
    for job in initial_jobs:
        print_job_request(job)
        fleet.submit_job(job)

    urgent_job = Job(
        "URGENT-001",
        "HIGH-VALUE",
        pickup=(1.4, 1.4),
        dropoff=(0.3, 0.3),
        priority=10,
    )
    urgent_submitted = False

    for tick in range(MAX_TICKS):
        if tick == DYNAMIC_JOB_TICK:
            print_job_request(urgent_job, dynamic=True)
            fleet.submit_job(urgent_job)
            urgent_submitted = True
            if all(robot.busy for robot in fleet):
                print("All robots are occupied; urgent job is pending without preemption.")

        fleet.step()
        print_events(fleet.pop_events())
        if tick % TELEMETRY_INTERVAL == 0:
            print_fleet_telemetry(fleet)
        if urgent_submitted and fleet.all_jobs_finished:
            break

    print("\n=====================================")
    print("NEXUS WAREHOUSE RESULT")
    print("=====================================\n")
    for job in fleet.jobs.values():
        duration = "n/a"
        if job.duration_ticks is not None:
            duration = f"{job.duration_ticks} ticks / {job.duration_ticks / 200.0:.2f}s"
        print(
            f"{job.job_id:<12} {job.status.value:<10} "
            f"Robot {job.assigned_robot_id or '-':<2} duration={duration}"
        )

    print("\nFleet:")
    print(f"{len(fleet.completed_jobs)} jobs completed")
    print(f"{len(fleet.failed_jobs)} job failures")
    print(f"{sum(robot.failed for robot in fleet)} robot failures")
    print(f"{fleet.steps} total ticks")

    success = (
        urgent_submitted
        and all(job.status is JobStatus.COMPLETED for job in fleet.jobs.values())
        and not fleet.failed
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
