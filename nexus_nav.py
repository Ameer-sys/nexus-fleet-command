import math

from bbsim.simulation import Simulation


def wrap_angle(angle):
    """Normalize angle into [-pi, pi]."""
    return (angle + math.pi) % (2 * math.pi) - math.pi


class NexusRobot:
    def __init__(self, robot_id, offset_x=0.0, offset_y=0.0):
        self.id = robot_id
        self.sim = Simulation(kind="arms")

        self.offset_x = offset_x
        self.offset_y = offset_y

        self.target = None
        self.arrived = False

    @property
    def position(self):
        return (
            float(self.sim.data.qpos[0]) + self.offset_x,
            float(self.sim.data.qpos[1]) + self.offset_y,
        )

    @property
    def yaw(self):
        """
        Extract world yaw from the robot base rotation matrix.
        Robot forward direction is its local +X axis.
        """
        rotation = self.sim.data.xmat[self.sim.base].reshape(3, 3)

        return math.atan2(
            rotation[1, 0],
            rotation[0, 0],
        )

    def set_target(self, x, y):
        self.target = (x, y)
        self.arrived = False

    def navigate(self):
        if self.target is None:
            self.sim.command[:] = [0.0, 0.0]
            return

        x, y = self.position
        tx, ty = self.target

        dx = tx - x
        dy = ty - y

        distance = math.hypot(dx, dy)

        # Close enough to target.
        if distance < 0.10:
            self.sim.command[:] = [0.0, 0.0]

            if not self.arrived:
                print(
                    f"✓ Robot {self.id} arrived at "
                    f"({tx:.2f}, {ty:.2f})"
                )

            self.arrived = True
            return

        desired_heading = math.atan2(dy, dx)

        heading_error = wrap_angle(
            desired_heading - self.yaw
        )

        # -----------------------------
        # Steering controller
        # -----------------------------

        # Turn harder when pointing away from target.
        yaw_command = max(
            -0.8,
            min(0.8, 1.8 * heading_error)
        )

        # Don't drive fast while facing the wrong direction.
        if abs(heading_error) > math.radians(45):
            velocity = 0.02

        elif abs(heading_error) > math.radians(20):
            velocity = 0.08

        else:
            velocity = min(
                0.18,
                max(0.05, distance * 0.4)
            )

        self.sim.command[:] = [
            velocity,
            yaw_command
        ]

    def step(self):
        self.navigate()
        self.sim.step()


# ==========================================
# NEXUS AUTONOMOUS NAVIGATION TEST
# ==========================================

robot = NexusRobot(
    robot_id="A",
    offset_x=0.0,
    offset_y=0.0,
)

robot.set_target(
    0.8,
    0.5,
)

MAX_STEPS = 5000

for step in range(MAX_STEPS):

    robot.step()

    if step % 200 == 0:

        x, y = robot.position

        print(
            f"step={step:4d} | "
            f"position=({x:.2f}, {y:.2f}) | "
            f"yaw={math.degrees(robot.yaw):.1f}° | "
            f"command={robot.sim.command}"
        )

    if robot.arrived:
        break


print("\n=== FINAL ===")

print("Position:", robot.position)
print("Target:", robot.target)
print("Failed:", robot.sim.failed)