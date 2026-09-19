"""NEXUS milestone #3: traffic coordination and autonomous job recovery."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus import (
    Fleet,
    Job,
    JobStatus,
    NexusRobot,
    TrafficConfig,
    TrafficManager,
)


MAX_TICKS = 12_000
PRIORITY_REQUEST_TICK = 1_200
STATUS_INTERVAL = 800
FAILURE_DELAY_AFTER_PICKUP = 200


def print_auction(fleet: Fleet, event: dict) -> None:
    job = fleet.jobs[event["job_id"]]
    label = "RECOVERY" if job.reassignment_count else "AUCTION"
    print(f"[{label}] {job.job_id} bids")
    for bid in event["bids"]:
        if bid["eligible"]:
            print(
                f"  {bid['robot_id']}: {bid['total']:.3f} "
                f"(distance {bid['distance']:.3f}, battery {bid['battery_penalty']:.3f})"
            )
    if event["winner"]:
        if job.reassignment_count:
            previous = job.assignment_history[-2]
            print(f"[RECOVERY] JOB REASSIGNED {previous} -> {event['winner']}")
        else:
            print(f"[JOB] {job.job_id} assigned -> {event['winner']}")


def print_events(fleet: Fleet, events: list[dict]) -> None:
    for event in events:
        event_type = event["type"]
        if event_type == "auction":
            print_auction(fleet, event)
        elif event_type == "traffic_conflict":
            first = fleet[event["robot_a"]]
            second = fleet[event["robot_b"]]
            print("\n[TRAFFIC] PREDICTED PATH CONFLICT")
            print(f"  Robot {event['robot_a']} vs Robot {event['robot_b']}")
            print(f"  Predicted separation: {event['predicted_distance']:.3f} m")
            print(
                f"  Priorities: {event['robot_a']}={first.current_job.priority if first.current_job else 0}, "
                f"{event['robot_b']}={second.current_job.priority if second.current_job else 0}"
            )
            print(f"  Right of way: Robot {event['right_of_way']}")
            print(f"  Reason: {event['reason']}")
            print(f"[TRAFFIC] Robot {event['yielding_robot']} yielding")
        elif event_type == "traffic_resumed":
            print(f"[TRAFFIC] PATH CLEAR - Robot {event['robot_id']} resumed")
        elif event_type == "package_acquired":
            print(
                f"[JOB] {event['robot_id']} acquired {event['package_id']} "
                f"for {event['job_id']}"
            )
        elif event_type == "delivery_started":
            print(f"[JOB] {event['job_id']} delivery leg started by {event['robot_id']}")
        elif event_type == "job_completed":
            print(f"[JOB] {event['job_id']} completed by {event['robot_id']}")
        elif event_type == "robot_unavailable":
            print(
                f"[FAULT] Robot {event['robot_id']} unavailable: {event['reason']} "
                f"at ({event['position'][0]:.3f}, {event['position'][1]:.3f})"
            )
        elif event_type == "job_requeued":
            if event["package_recovery_point"]:
                point = event["package_recovery_point"]
                print(f"[RECOVERY] PACKAGE RECOVERY POINT ({point[0]:.3f}, {point[1]:.3f})")
            print(f"[RECOVERY] {event['job_id']} requeued automatically")


def print_status(fleet: Fleet) -> None:
    print(f"\n[STATUS] tick={fleet.steps}")
    for robot in fleet:
        item = robot.telemetry()
        availability = "AVAILABLE" if item["available"] else "UNAVAILABLE"
        print(
            f"  {robot.id} {availability:<11} {item['task_state']:<10} "
            f"job={item['job_id'] or '-':<10} "
            f"position=({item['x']:.2f}, {item['y']:.2f})"
        )
    pending = [job.job_id for job in fleet.pending_jobs]
    if pending:
        print(f"  pending={','.join(pending)}")


def main() -> int:
    traffic = TrafficManager(
        TrafficConfig(
            safety_radius=0.16,
            nominal_speed=0.15,
            evaluation_interval_ticks=40,
            minimum_yield_ticks=160,
            clear_evaluations_to_resume=2,
            resume_cooldown_ticks=200,
            yield_timeout_ticks=2_400,
        )
    )
    fleet = Fleet(
        [
            NexusRobot("A", offset=(0.0, 0.0)),
            NexusRobot("B", offset=(2.0, 0.0)),
            NexusRobot("C", offset=(0.0, 2.0)),
        ],
        traffic_manager=traffic,
    )

    initial_jobs = [
        Job("JOB-A", "PRIORITY-PART", (0.2, 0.1), (1.8, 1.8), priority=8),
        Job("JOB-B", "STANDARD-BOX", (1.8, 0.1), (0.2, 1.8), priority=3),
        Job("JOB-C", "SENSOR-KIT", (0.1, 1.8), (0.4, 1.4), priority=5),
    ]
    for job in initial_jobs:
        fleet.submit_job(job)

    urgent = Job(
        "JOB-URGENT",
        "MEDICAL-CRITICAL",
        pickup=(0.6, 1.3),
        dropoff=(1.7, 0.3),
        priority=10,
    )
    urgent_submitted = False
    failure_injected = False

    print("=================================================")
    print("NEXUS WAREHOUSE ONLINE")
    print("=================================================")
    print("3 robots")
    print("3 initial jobs")
    print("Traffic prediction: ONLINE")
    print("Automatic recovery: ONLINE\n")

    for tick in range(MAX_TICKS):
        if tick == PRIORITY_REQUEST_TICK:
            print("\n!!! PRIORITY REQUEST !!!")
            print(f"Package: {urgent.package_id}")
            print(f"Priority: {urgent.priority}")
            fleet.submit_job(urgent)
            urgent_submitted = True

        to_dropoff = next(
            (
                event
                for event in reversed(urgent.history)
                if event.event == "to_dropoff"
            ),
            None,
        )
        if (
            urgent_submitted
            and not failure_injected
            and urgent.status is JobStatus.TO_DROPOFF
            and to_dropoff is not None
            and fleet.steps - to_dropoff.tick >= FAILURE_DELAY_AFTER_PICKUP
            and urgent.assigned_robot_id is not None
        ):
            failed_id = urgent.assigned_robot_id
            print(f"\n!!! ROBOT {failed_id} UNAVAILABLE !!!")
            print(f"Current task: {urgent.package_id}")
            print("NEXUS RECOVERY INITIATED")
            fleet.inject_operational_failure(failed_id, "blocked aisle")
            failure_injected = True

        fleet.step()
        print_events(fleet, fleet.pop_events())
        if tick % STATUS_INTERVAL == 0:
            print_status(fleet)
        if urgent_submitted and failure_injected and fleet.all_jobs_finished:
            break

    stats = fleet.stats()
    print("\n=================================================")
    print("NEXUS AUTONOMOUS WAREHOUSE")
    print("=================================================")
    print(f"Jobs completed: {stats['jobs_completed']}")
    print(f"Robot failures/unavailable: {stats['robots_unavailable']}")
    print(f"Automatic reassignments: {stats['automatic_reassignments']}")
    print(f"Traffic conflicts prevented: {stats['traffic_conflicts_prevented']}")
    print(f"Manual interventions: {stats['manual_interventions']}")
    print(f"Runtime: {fleet.steps} ticks / {fleet.steps / 200.0:.2f} simulated seconds\n")
    for job in fleet.jobs.values():
        route = " -> ".join(job.assignment_history)
        print(f"{job.job_id:<11} {job.status.value:<10} robots={route}")

    success = (
        all(job.status is JobStatus.COMPLETED for job in fleet.jobs.values())
        and stats["robots_unavailable"] == 1
        and stats["automatic_reassignments"] == 1
        and stats["traffic_conflicts_prevented"] >= 1
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
