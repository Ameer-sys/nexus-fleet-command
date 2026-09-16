import numpy as np
import pytest

from bbsim.drive import DriveControls
from bbsim.simulation import Simulation


@pytest.mark.parametrize("kind", ["arms", "terrain", "lean"])
def test_press_hold_combine_and_release(kind):
    sim = Simulation(kind, velocity=0.15)
    controls = DriveControls(sim)
    np.testing.assert_array_equal(sim.command, [0, 0])
    controls.press("W")
    controls.press("A")
    np.testing.assert_allclose(sim.command, [0.15, 0.6])
    controls.release("W")
    np.testing.assert_allclose(sim.command, [0, 0.6])
    controls.release("A")
    np.testing.assert_array_equal(sim.command, [0, 0])
    controls.press("S")
    controls.press("D")
    np.testing.assert_allclose(sim.command, [-0.15, -0.6])
    controls.press("W")
    controls.press("A")
    np.testing.assert_array_equal(sim.command, [0, 0])


def test_keyboard_and_mouse_holds_are_independent():
    sim = Simulation("terrain")
    c = DriveControls(sim)
    c.press("W")
    c.press("A", "mouse")
    c.release_mouse()
    np.testing.assert_allclose(sim.command, [0.15, 0])
    c.press("W", "mouse")
    c.release("W")
    np.testing.assert_allclose(sim.command, [0.15, 0])
    c.release_mouse()
    np.testing.assert_array_equal(sim.command, [0, 0])


@pytest.mark.parametrize("action", ["stop", "pause", "reset", "focus"])
def test_stop_paths_clear_held_commands(action):
    sim = Simulation("arms")
    c = DriveControls(sim)
    c.press("W")
    c.press("D", "mouse")
    if action == "focus":
        c.focus(False)
    else:
        c.activate(action)
    assert not c.keys
    np.testing.assert_array_equal(sim.command, [0, 0])
    c.focus(True)
    if c.paused:
        c.activate("pause")
    c.update()
    np.testing.assert_array_equal(sim.command, [0, 0])


def test_lean_driving_and_contact_modes_survive_reset():
    sim = Simulation("lean")
    c = DriveControls(sim)
    assert sim.lean_degrees == 0
    assert sim.data.mocap_pos[0, 2] == -5
    c.activate("reset")
    assert sim.lean_degrees == 0
    assert sim.data.mocap_pos[0, 2] == -5
    c.activate("lean")
    assert sim.lean_degrees == 3
    assert sim.data.mocap_pos[0, 2] == pytest.approx(0.85)
    c.activate("angle+")
    assert sim.lean_degrees == 4
    c.activate("reset")
    assert sim.lean_degrees == 4
    c.activate("lean")
    assert sim.lean_degrees == 0
    assert sim.data.mocap_pos[0, 2] == -5


@pytest.mark.parametrize("kind", ["arms", "terrain", "lean"])
def test_manual_drive_moves_robot_and_release_clears_command(kind):
    sim = Simulation(kind)
    controls = DriveControls(sim)
    controls.press("W")
    for _ in range(1000):
        controls.update()
        sim.step()
        assert sim.failed is None
    assert sim.data.qpos[0] > 0.25
    controls.release("W")
    for _ in range(1000):
        controls.update()
        sim.step()
        assert sim.failed is None
    np.testing.assert_array_equal(sim.command, [0, 0])


def test_speed_controls_stay_within_supported_range():
    sim = Simulation("terrain")
    c = DriveControls(sim)
    for _ in range(50):
        c.activate("speed+")
        c.activate("turn+")
    assert c.speed == 0.3 and c.turn_speed == 1.0
    for _ in range(50):
        c.activate("speed-")
        c.activate("turn-")
    assert c.speed == 0.05 and c.turn_speed == 0.1


def test_window_callback_release_repeat_and_focus_handling():
    import glfw

    from bbsim.drive_ui import DriveWindow

    # Exercise the actual GLFW adapter without opening a window in the test suite.
    viewer = DriveWindow.__new__(DriveWindow)
    viewer.sim = Simulation("terrain")
    viewer.controls = DriveControls(viewer.sim)
    viewer._key(None, glfw.KEY_W, 0, glfw.PRESS, 0)
    viewer._key(None, glfw.KEY_A, 0, glfw.PRESS, 0)
    np.testing.assert_allclose(viewer.sim.command, [0.15, 0.6])
    viewer._key(None, glfw.KEY_W, 0, glfw.RELEASE, 0)
    np.testing.assert_allclose(viewer.sim.command, [0, 0.6])
    viewer._key(None, glfw.KEY_SPACE, 0, glfw.PRESS, 0)
    viewer._key(None, glfw.KEY_A, 0, glfw.REPEAT, 0)
    np.testing.assert_array_equal(viewer.sim.command, [0, 0])
    viewer._key(None, glfw.KEY_W, 0, glfw.PRESS, glfw.MOD_SUPER)
    np.testing.assert_array_equal(viewer.sim.command, [0, 0])
    viewer._key(None, glfw.KEY_W, 0, glfw.PRESS, 0)
    viewer._focus(None, False)
    np.testing.assert_array_equal(viewer.sim.command, [0, 0])
