from bbsim.simulation import Simulation


class NexusRobot:
    def __init__(self, robot_id, offset_x=0.0, offset_y=0.0):
        self.id = robot_id
        self.sim = Simulation(kind="arms")

        # Logical warehouse location.
        # Each MuJoCo instance starts at (0,0), so NEXUS offsets them
        # into one shared virtual warehouse.
        self.offset_x = offset_x
        self.offset_y = offset_y

        self.busy = False
        self.battery = 100.0
        self.task = None

    @property
    def position(self):
        return (
            float(self.sim.data.qpos[0]) + self.offset_x,
            float(self.sim.data.qpos[1]) + self.offset_y,
        )

    def command(self, velocity, yaw):
        self.sim.command[:] = [velocity, yaw]

    def stop(self):
        self.sim.command[:] = [0.0, 0.0]

    def step(self):
        self.sim.step()

    @property
    def failed(self):
        return self.sim.failed


# ----------------------------------
# Create the NEXUS fleet
# ----------------------------------

robots = {
    "A": NexusRobot("A", 0.0, 0.0),
    "B": NexusRobot("B", 2.0, 0.0),
    "C": NexusRobot("C", 0.0, 2.0),
}


# Robot A: forward
robots["A"].command(0.15, 0.0)

# Robot B: drive while turning
robots["B"].command(0.12, 0.25)

# Robot C: stationary
robots["C"].stop()


STEPS = 800

for step in range(STEPS):

    for robot in robots.values():
        robot.step()

    if step % 200 == 0:

        print(f"\n--- NEXUS FLEET | STEP {step} ---")

        for robot in robots.values():

            x, y = robot.position

            print(
                f"Robot {robot.id} | "
                f"x={x:.3f} "
                f"y={y:.3f} "
                f"failed={robot.failed}"
            )


for robot in robots.values():
    robot.stop()


print("\n=== FINAL NEXUS FLEET STATE ===")

for robot in robots.values():

    x, y = robot.position

    print(
        f"Robot {robot.id}: "
        f"({x:.3f}, {y:.3f})"
    )