import json
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest

from bbsim.config import ARM_JOINTS, ASSETS, MODEL_DIR

REFERENCE = json.loads((ASSETS / "reference/training_dynamics.json").read_text())
MAPPING = json.loads((MODEL_DIR / "conversion.json").read_text())["reference_to_link"]


@pytest.mark.parametrize("model_file", ["chopped_policy.urdf", "arms.xml"])
def test_mass_com_inertia_and_kinematics_against_training_samples(model_file):
    m = mujoco.MjModel.from_xml_path(str(MODEL_DIR / model_file))
    d = mujoco.MjData(m)
    assert abs(m.body_mass.sum() - 13.605122) < 1e-12
    for b in REFERENCE["bodies"]:
        i = m.body(MAPPING[b["name"]]).id
        assert m.body_mass[i] == pytest.approx(b["mass"], abs=1e-14)
        np.testing.assert_allclose(m.body_ipos[i], b["ipos"], rtol=0, atol=1e-13)
        np.testing.assert_allclose(m.body_inertia[i], b["inertia"], rtol=0, atol=1e-11)
    # Compare every body's world COM and principal inertia frame at 16 independent
    # articulated poses exported on the training host, not recomputed by this builder.
    for sample in REFERENCE["samples"]:
        mujoco.mj_resetData(m, d)
        for name, value in zip(ARM_JOINTS, sample["q"]):
            d.qpos[m.joint(name).qposadr[0]] = value
        mujoco.mj_forward(m, d)
        for k, b in enumerate(REFERENCE["bodies"]):
            i = m.body(MAPPING[b["name"]]).id
            np.testing.assert_allclose(d.xpos[i], sample["xpos"][k], rtol=0, atol=1e-12)
            np.testing.assert_allclose(d.xipos[i], sample["xipos"][k], rtol=0, atol=1e-12)
            # Sign-flipped principal eigenvectors can encode the same tensor.
            expected_r = np.asarray(sample["ximat"][k]).reshape(3, 3)
            actual_r = d.ximat[i].reshape(3, 3)
            expected = expected_r @ np.diag(b["inertia"]) @ expected_r.T
            actual = actual_r @ np.diag(m.body_inertia[i]) @ actual_r.T
            # URDF tensors undergo MuJoCo's approximate eigen-decomposition;
            # the MJCF carries the original principal axes without re-solving.
            np.testing.assert_allclose(
                actual, expected, rtol=0, atol=1e-6 if model_file.endswith(".urdf") else 1e-12
            )


def test_urdf_stores_the_full_reference_tensor_without_inertial_rpy():
    from scipy.spatial.transform import Rotation

    urdf = ET.parse(MODEL_DIR / "chopped_policy.urdf")
    for b in REFERENCE["bodies"]:
        element = urdf.find(f"link[@name='{MAPPING[b['name']]}']/inertial")
        assert element.find("origin").get("rpy") == "0 0 0"
        v = {k: float(x) for k, x in element.find("inertia").attrib.items()}
        actual = np.array(
            [
                [v["ixx"], v["ixy"], v["ixz"]],
                [v["ixy"], v["iyy"], v["iyz"]],
                [v["ixz"], v["iyz"], v["izz"]],
            ]
        )
        r = Rotation.from_quat(b["iquat"], scalar_first=True).as_matrix()
        expected = r @ np.diag(b["inertia"]) @ r.T
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-15)


@pytest.mark.parametrize(
    "kind,joints,actuators", [("arms", 17, 16), ("terrain", 3, 2), ("lean", 3, 2)]
)
def test_model_topology_and_physics(kind, joints, actuators):
    m = mujoco.MjModel.from_xml_path(str(MODEL_DIR / f"{kind}.xml"))
    assert m.njnt == joints and m.nu == actuators
    assert m.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE
    assert m.opt.timestep == 0.005
    assert m.body_mass.sum() == pytest.approx(13.605122, abs=1e-12)
    assert np.count_nonzero(m.geom_contype) == (5 if kind == "lean" else 3)
    for c in REFERENCE["contacts"]:
        i = m.geom(c["name"]).id
        np.testing.assert_allclose(m.geom_pos[i], c["pos"], rtol=0, atol=1e-15)
        np.testing.assert_allclose(m.geom_size[i], c["size"], rtol=0, atol=1e-15)
    for side in ("left", "right"):
        cfg = json.loads((ASSETS / f"reference/wheel_{side}.json").read_text())
        i = m.joint(side).dofadr[0]
        assert m.dof_armature[i] == pytest.approx(round(cfg["i_wheel"] - 0.00357964, 6))
        assert m.dof_frictionloss[i] == cfg["frictionloss"]
        assert m.dof_damping[i] == cfg["viscous"]
    meshes = ET.parse(MODEL_DIR / f"{kind}.xml").findall("asset/mesh")
    originals = {p.name for p in (ASSETS / "source/chopped_urdf_v2/meshes").glob("*.stl")}
    assert all(e.get("file") in originals for e in meshes)
