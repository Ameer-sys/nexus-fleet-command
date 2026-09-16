import pytest

from bbsim.simulation import Simulation


@pytest.mark.parametrize(
    "kind,kwargs",
    [
        ("arms", {}),
        ("terrain", {}),
        ("lean", {}),
        ("arms", dict(arm_motion=True, velocity=0.15, yaw=0.25)),
        ("terrain", dict(velocity=0.15, terrain="bumps")),
        ("terrain", dict(velocity=0.15, terrain="slope")),
    ],
)
def test_nominal_twenty_second_playback(kind, kwargs):
    sim = Simulation(kind, **kwargs)
    for _ in range(4000):
        sim.step()
        assert sim.failed is None, sim.summary()
    assert sim.data.time == pytest.approx(20.0)
    assert sim.stats["max_abs_pitch_deg"] < 20
    if kind == "lean":
        assert sim.stats["max_wall_force_n"] > 0
        assert abs(sim.summary()["pitch_deg"] - 3.0) < 1


def test_reset_clears_policy_and_motor_history():
    sim = Simulation("arms", arm_motion=True)
    for _ in range(200):
        sim.step()
    sim.reset()
    other = Simulation("arms", arm_motion=True)
    for _ in range(100):
        sim.step()
        other.step()
    import numpy as np

    np.testing.assert_array_equal(sim.data.qpos, other.data.qpos)
    np.testing.assert_array_equal(sim.last_obs, other.last_obs)
