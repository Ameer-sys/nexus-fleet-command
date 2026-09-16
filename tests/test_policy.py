import hashlib
import json

import numpy as np
import onnx
import pytest

from bbsim.config import ASSETS
from bbsim.policy import (
    ARM_POSITION_SCALE,
    ARM_VELOCITY_SCALE,
    Actor,
    HallEstimator,
    Observations,
    WheelMotor,
)


@pytest.mark.parametrize(
    "kind,fixture", [("arms", "policies/arms_parity.npz"), ("lean", "reference/lean_c_parity.npz")]
)
def test_actor_against_independent_reference(kind, fixture):
    z = np.load(ASSETS / fixture)
    actor = Actor(kind)
    actual = np.array([actor(x) for x in z["obs"]])
    # C's four-accumulator dot products and ONNX's fused float32 GEMMs differ
    # in reduction/ELU rounding. Lean's stress corpus includes large inputs.
    np.testing.assert_allclose(
        actual, z["actions"], atol=1.5e-5 if kind == "lean" else 2e-6, rtol=0
    )


def test_lean_weights_are_bit_exact_to_deployed_export():
    model = onnx.load(ASSETS / "policies/lean.onnx")
    weights = {v.name: onnx.numpy_helper.to_array(v) for v in model.graph.initializer}
    source = np.load(ASSETS / "policies/lean_weights.npz")
    for layer, shape in enumerate([(256, 19), (128, 256), (4, 128)]):
        np.testing.assert_array_equal(weights[f"W{layer}"], source[f"W{layer}"].reshape(shape).T)
        np.testing.assert_array_equal(weights[f"B{layer}"], source[f"B{layer}"])


@pytest.mark.parametrize("side", ["left", "right"])
def test_motor_recurrence_against_training_torch(side):
    z = np.load(ASSETS / f"reference/wheel_{side}_parity.npz")
    motor = WheelMotor(side)
    np.testing.assert_allclose(
        [motor.residual(x) for x in z["inputs"]], z["outputs"], atol=1e-6, rtol=0
    )
    motor.reset()
    assert motor(0.01, 0) == pytest.approx(0.01)  # stick gate suppresses rest bias


def test_artifact_identity():
    hashes = json.loads((ASSETS / "policies/provenance.json").read_text())
    for kind in ["arms", "terrain", "lean"]:
        assert (
            hashlib.sha256((ASSETS / f"policies/{kind}.onnx").read_bytes()).hexdigest()
            == hashes[kind]["sha256"]
        )
    assert (
        hashes["terrain"]["sha256"]
        == "ea9bc8ec6c94b34d9eade03e2435f47bf515ed19857555e961f5668f8c475165"
    )


def test_observation_contract_and_sample_hold():
    obs = Observations("arms")
    pos = np.arange(14) * 0.02
    vel = np.arange(14) * 0.1
    command = np.array([0.12, -0.4])

    def build(step):
        return obs.build(
            np.zeros(2),
            np.array([-10.0, 6.0]),
            0.12,
            np.array([0.0, step + 0.2, 0.3]),
            0.08,
            command,
            pos + step,
            vel + step,
            ideal_wheels=True,
        )

    x = build(0)
    np.testing.assert_allclose(x[:3], [0.5, 0.3, 0.48])
    np.testing.assert_allclose(x[3:8], np.full(5, 0.2))
    np.testing.assert_allclose(x[8:11], [0.12, 0.48, -0.4])
    np.testing.assert_allclose(x[17:31], pos * ARM_POSITION_SCALE, atol=1e-7)
    np.testing.assert_allclose(
        x[32:].reshape(3, 14), np.tile(vel * ARM_VELOCITY_SCALE, (3, 1)), atol=1e-7
    )
    for step in range(1, 5):
        obs.remember_action([step * 0.1, -step * 0.1])
        x = build(step)
        if step < 4:
            np.testing.assert_allclose(x[17:31], pos * ARM_POSITION_SCALE, atol=1e-7)
    np.testing.assert_allclose(x[3:8], [0.2, 1.2, 2.2, 3.2, 4.2], atol=3e-7)
    np.testing.assert_allclose(x[11:17], [0.2, -0.2, 0.3, -0.3, 0.4, -0.4], atol=1e-7)
    np.testing.assert_allclose(x[17:31], (pos + 4) * ARM_POSITION_SCALE, atol=3e-7)
    np.testing.assert_allclose(
        x[32:60].reshape(2, 14), np.tile(vel * ARM_VELOCITY_SCALE, (2, 1)), atol=1e-7
    )
    np.testing.assert_allclose(x[60:], (vel + 4) * ARM_VELOCITY_SCALE, atol=3e-7)
    assert x[31] == pytest.approx(2 * 4 * (0.12 - 0.08) * 0.005)


def test_hall_estimator_signed_speed_and_decay():
    estimator = HallEstimator()
    for i in range(500):
        speed = estimator(np.array([-3.0, 5.0]) * i * 0.005)
    np.testing.assert_allclose(speed, [-3.0, 5.0], atol=0.025, rtol=0)
    for i in range(500):
        speed = estimator(np.array([-3.0, 5.0]) * 499 * 0.005)
    np.testing.assert_array_equal(speed, [0.0, 0.0])


def test_lean_slot_is_commanded_angle_not_binary_flag():
    o = Observations("lean")
    x = o.build(
        np.zeros(2),
        np.zeros(2),
        0.0,
        np.zeros(3),
        0.0,
        np.zeros(2),
        np.zeros(14),
        np.zeros(14),
        lean_degrees=9.0,
    )
    assert x.shape == (19,) and x[18] == 3.0
