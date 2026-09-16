"""Policy inference, training observation layout, and deterministic wheel plant."""

import json

import numpy as np
import onnxruntime as ort
from scipy.special import expit

from .config import ASSETS, DT, POLICY_INPUTS

ARM_POSITION_SCALE = np.tile(
    [
        1.0,
        0.49822416967897676,
        0.6740679942715567,
        0.49822416967897676,
        0.520870722846203,
        0.6740679942715567,
        0.6740679942715567,
    ],
    2,
)
ARM_VELOCITY_SCALE = np.tile([2.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4], 2)


class Actor:
    def __init__(self, kind):
        self.kind = kind
        self.size = POLICY_INPUTS[kind]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(ASSETS / f"policies/{kind}.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self.input = self.session.get_inputs()[0].name
        assert self.session.get_inputs()[0].shape == [1, self.size]

    def __call__(self, obs):
        x = np.asarray(obs, dtype=np.float32).reshape(1, self.size)
        if not np.isfinite(x).all():
            raise FloatingPointError("Nonfinite policy observation")
        y = self.session.run(None, {self.input: x})[0].reshape(2)
        if not np.isfinite(y).all() or np.max(np.abs(y)) > 1.00001:
            raise FloatingPointError("Invalid policy action")
        return np.clip(y, -1, 1)


class WheelMotor:
    """Residual LSTM motor model; torques use the MuJoCo joint frame."""

    def __init__(self, side):
        self.config = json.loads((ASSETS / f"reference/wheel_{side}.json").read_text())
        with np.load(ASSETS / f"reference/wheel_{side}.npz") as z:
            self.weights = {k: z[k] for k in z.files}
        self.reset()

    def reset(self):
        c = self.config
        self.h = np.zeros((c["layers"], c["hid"]), np.float32)
        self.c = np.zeros_like(self.h)

    def residual(self, inputs):
        x = np.asarray(inputs, np.float32)
        w = self.weights
        for i in range(self.config["layers"]):
            p = f"cells.{i}."
            gates = (
                w[p + "weight_ih"] @ x
                + w[p + "bias_ih"]
                + w[p + "weight_hh"] @ self.h[i]
                + w[p + "bias_hh"]
            )
            ig, fg, gg, og = np.split(gates, 4)
            self.c[i] = expit(fg) * self.c[i] + expit(ig) * np.tanh(gg)
            self.h[i] = expit(og) * np.tanh(self.c[i])
            x = self.h[i]
        x = w["head.0.weight"] @ x + w["head.0.bias"]
        x = x / (1 + np.abs(x))
        return float((w["head.2.weight"] @ x + w["head.2.bias"])[0])

    def __call__(self, torque, velocity):
        c = self.config
        sign = c.get("sign", 1.0)
        tau, vel = torque * sign, velocity * sign
        residual = self.residual([tau * c["pos_scale"], vel * c["vel_scale"]]) * c.get(
            "torque_scale", 1.0
        )
        top = min(c["dc_sat"] * (1 - vel / c["dc_vlim"]), c["dc_elim"])
        bottom = max(c["dc_sat"] * (-1 - vel / c["dc_vlim"]), -c["dc_elim"])
        base = min(max(tau, bottom), top)
        if (
            abs(vel) < c.get("stick_gate_w_eps", c.get("w_eps", 0.15))
            and abs(base) < c["frictionloss"]
        ):
            residual = 0.0
        return (base + residual) * sign


class HallEstimator:
    """Training's interpolated 90-count/rev, 125-us 1/T wheel estimator."""

    def __init__(self):
        self.previous = None
        self.age = np.zeros(2)
        self.period = np.full(2, 2.0)
        self.direction = np.zeros(2)

    def __call__(self, radians):
        x = np.asarray(radians) * (90 / (2 * np.pi))
        if self.previous is None:
            self.previous = x.copy()
        dx = x - self.previous
        delta = np.round(x) - np.round(self.previous)
        edge = delta != 0
        direction = np.sign(dx)
        safe_dx = np.where(edge, dx, 1.0)
        first = np.clip((np.round(self.previous) + 0.5 * direction - self.previous) / safe_dx, 0, 1)
        last = np.clip((np.round(x) - 0.5 * direction - self.previous) / safe_dx, 0, 1)
        period = np.where(
            np.abs(delta) >= 2, DT / np.maximum(np.abs(dx), 1e-6), self.age + DT * first
        )
        period = np.minimum(np.maximum(np.round(period / 125e-6), 1) * 125e-6, 2.0)
        self.period = np.where(edge, period, self.period)
        self.direction = np.where(edge, direction, self.direction)
        self.age = np.where(edge, DT * (1 - last), self.age + DT)
        self.previous = x.copy()
        velocity = self.direction / np.maximum(self.period, self.age) * (2 * np.pi / 90)
        return np.where(np.abs(velocity) < 0.044, 0.0, velocity)


class Observations:
    def __init__(self, kind):
        self.kind = kind
        self.pitch_rates = np.zeros(5)
        self.actions = np.zeros((3, 2))
        self.arm_position = np.zeros(14)
        self.arm_velocity = np.zeros((3, 14))
        self.arc_error = 0.0
        self.step = 0
        self.hall = HallEstimator()

    def build(
        self,
        wheel_pos,
        wheel_vel,
        pitch,
        gyro,
        body_velocity,
        command,
        arm_position,
        arm_velocity,
        lean_degrees=3.0,
        ideal_wheels=False,
    ):
        # Histories are oldest -> newest, including the current gyro sample.
        if self.step == 0:
            self.pitch_rates[:] = gyro[1]
        self.pitch_rates[:-1] = self.pitch_rates[1:]
        self.pitch_rates[-1] = gyro[1]
        if self.step % 4 == 0:
            self.arm_position[:] = arm_position
            if self.step == 0:
                self.arm_velocity[:] = arm_velocity
            else:
                self.arm_velocity[:-1] = self.arm_velocity[1:]
                self.arm_velocity[-1] = arm_velocity
        # Training ARC uses true body-frame forward velocity, with ±0.5 m clamp.
        if self.step:
            self.arc_error = np.clip(self.arc_error + (command[0] - body_velocity) * DT, -0.5, 0.5)
        velocity = wheel_vel if ideal_wheels else self.hall(wheel_pos)
        common = np.r_[
            velocity * np.array([-0.05, 0.05]),
            pitch * 4,
            self.pitch_rates,
            gyro[2] * 0.4,
            command * np.array([4.0, 1.0]),
            self.actions.ravel(),
        ]
        if self.kind == "arms":
            obs = np.r_[
                common,
                self.arm_position * ARM_POSITION_SCALE,
                self.arc_error * 2,
                (self.arm_velocity * ARM_VELOCITY_SCALE).ravel(),
            ]
        elif self.kind == "lean":
            obs = np.r_[common, self.arc_error * 2, lean_degrees / 3.0]
        else:
            obs = np.r_[common, self.arc_error * 2]
        self.step += 1
        return obs.astype(np.float32)

    def remember_action(self, action):
        self.actions[:-1] = self.actions[1:]
        self.actions[-1] = action
