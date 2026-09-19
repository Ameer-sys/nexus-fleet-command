from bbsim.simulation import Simulation

robot = Simulation(kind="arms")

print("Starting position:", robot.data.qpos[:3])

# Drive forward for ~2 seconds
robot.command[:] = [0.15, 0.0]

for _ in range(400):
    robot.step()

robot.command[:] = [0.0, 0.0]

print("\nAfter driving:")
print(robot.summary())