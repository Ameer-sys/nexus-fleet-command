"""Export dynamics and motor weights on the training host (PYTHONPATH=src)."""

import argparse
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np
import torch
from bb_mjlab.tasks.arms_v3_twist_arms_motion.robot_cfg import get_motion_spec
from bb_mjlab.wheel_actuators import WheelLSTM


def export(out):
    out.mkdir(parents=True, exist_ok=True)
    model = get_motion_spec().compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    bodies = []
    for i in range(1, model.nbody):
        body = dict(
            name=model.body(i).name,
            parent=model.body(int(model.body_parentid[i])).name,
            pos=model.body_pos[i].tolist(),
            quat=model.body_quat[i].tolist(),
            mass=float(model.body_mass[i]),
            ipos=model.body_ipos[i].tolist(),
            iquat=model.body_iquat[i].tolist(),
            inertia=model.body_inertia[i].tolist(),
            xpos=data.xpos[i].tolist(),
            xquat=data.xquat[i].tolist(),
        )
        js = []
        for j in range(model.njnt):
            if model.jnt_bodyid[j] == i:
                js.append(
                    dict(
                        name=model.joint(j).name,
                        type=int(model.jnt_type[j]),
                        axis=model.jnt_axis[j].tolist(),
                        pos=model.jnt_pos[j].tolist(),
                        range=model.jnt_range[j].tolist(),
                        limited=bool(model.jnt_limited[j]),
                    )
                )
        body["joints"] = js
        bodies.append(body)
    contacts = []
    for i in range(model.ngeom):
        if model.geom_contype[i]:
            contacts.append(
                dict(
                    name=model.geom(i).name,
                    body=model.body(int(model.geom_bodyid[i])).name,
                    type=int(model.geom_type[i]),
                    pos=model.geom_pos[i].tolist(),
                    quat=model.geom_quat[i].tolist(),
                    size=model.geom_size[i].tolist(),
                    friction=model.geom_friction[i].tolist(),
                )
            )
    source = Path("bb-urdf/mjcf/bb_v2.xml")
    payload = dict(
        mujoco_version=mujoco.__version__,
        source=str(source.resolve()),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        bodies=bodies,
        contacts=contacts,
    )
    rng = np.random.default_rng(738)
    samples = []
    names = [f"{side}J{i}" for side in "LR" for i in range(1, 8)]
    for sample in range(16):
        mujoco.mj_resetData(model, data)
        q = np.zeros(14) if sample == 0 else rng.uniform(-0.4, 0.4, 14)
        if sample:
            q[[0, 7]] = rng.uniform(-0.8, -0.2, 2)
        for name, value in zip(names, q):
            data.qpos[model.joint(name).qposadr[0]] = value
        mujoco.mj_forward(model, data)
        samples.append(
            dict(
                q=q.tolist(),
                xpos=data.xpos[1:].tolist(),
                xipos=data.xipos[1:].tolist(),
                ximat=data.ximat[1:].tolist(),
            )
        )
    payload["samples"] = samples
    (out / "training_dynamics.json").write_text(json.dumps(payload, indent=2) + "\n")
    source_dir = Path("src/bb_mjlab/wheel_models/v3")
    for side, suffix in [("left", "left"), ("right", "bal")]:
        config_path = source_dir / f"cfg_residual_{suffix}.json"
        config = json.loads(config_path.read_text())
        weights_path = source_dir / f"state_residual_{suffix}.pt"
        weights = torch.load(weights_path, map_location="cpu", weights_only=True)
        np.savez(out / f"wheel_{side}.npz", **{k: v.numpy() for k, v in weights.items()})
        config["source_weights_sha256"] = hashlib.sha256(weights_path.read_bytes()).hexdigest()
        (out / f"wheel_{side}.json").write_text(json.dumps(config, indent=2) + "\n")
        net = WheelLSTM(config["hid"], config["layers"])
        net.load_state_dict(weights)
        net.eval()
        x = rng.normal(size=(512, 2)).astype(np.float32)
        h = [torch.zeros(1, config["hid"]) for _ in range(config["layers"])]
        c = [torch.zeros_like(v) for v in h]
        expected = []
        with torch.no_grad():
            for row in x:
                y, h, c = net.step(torch.from_numpy(row[None]), h, c)
                expected.append(y.item())
        np.savez(out / f"wheel_{side}_parity.npz", inputs=x, outputs=np.array(expected))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output)
