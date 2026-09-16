"""Rebuild the small robot from chopped visuals and numeric training dynamics."""

import csv
import json
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation

from .config import ARM_JOINTS, ASSETS, DT, MODEL_DIR, WHEELS


def numbers(x):
    return " ".join(f"{float(v):.16g}" for v in np.asarray(x).flat)


def transform(pos=(0, 0, 0), quat=None, rpy=None):
    t = np.eye(4)
    t[:3, 3] = pos
    if quat is not None:
        t[:3, :3] = Rotation.from_quat(quat, scalar_first=True).as_matrix()
    elif rpy is not None:
        t[:3, :3] = Rotation.from_euler("xyz", rpy).as_matrix()
    return t


def origin(e):
    o = e.find("origin")
    if o is None:
        return np.eye(4)
    return transform(
        np.fromstring(o.get("xyz", "0 0 0"), sep=" "),
        rpy=np.fromstring(o.get("rpy", "0 0 0"), sep=" "),
    )


def urdf_origin(parent, t):
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Gimbal lock detected")
        angles = Rotation.from_matrix(t[:3, :3]).as_euler("xyz")
    ET.SubElement(parent, "origin", xyz=numbers(t[:3, 3]), rpy=numbers(angles))


def reference_mapping(reference):
    result = {}
    for b in reference["bodies"]:
        js = b["joints"]
        name = js[0]["name"] if js else ""
        if name in WHEELS:
            result[b["name"]] = name + "_wheel"
        elif name and name[0] in "LR" and name[1] == "J":
            result[b["name"]] = name[0].lower() + "j" + str(int(name[2:]) - 1) + "_link"
        elif b["parent"] == "world":
            result[b["name"]] = "base"
    # Training fixes the grippers to the hands.
    for b in reference["bodies"]:
        if b["name"] not in result:
            parent = result[b["parent"]]
            siblings = [x for x in reference["bodies"] if x["parent"] == b["parent"]]
            result[b["name"]] = parent[:1] + f"_finger{siblings.index(b) + 1}"
    return result


def add_urdf_joint(robot, body, mapping):
    name = mapping[body["name"]]
    source = body["joints"][0] if body["joints"] else None
    if name.endswith("_link"):
        joint_name = name.removesuffix("_link")
    elif source:
        joint_name = source["name"]
    else:
        joint_name = name + "_fixed"

    if source is None:
        joint_type = "fixed"
    elif source["type"] == 2:
        joint_type = "prismatic"
    elif source["limited"]:
        joint_type = "revolute"
    else:
        joint_type = "continuous"

    joint = ET.SubElement(robot, "joint", name=joint_name, type=joint_type)
    ET.SubElement(joint, "parent", link=mapping[body["parent"]])
    ET.SubElement(joint, "child", link=name)
    urdf_origin(joint, transform(body["pos"], body["quat"]))
    if source is None:
        return
    if not np.allclose(source["pos"], 0):
        raise ValueError("Exporter requires joint anchors at body origins")
    if joint_type == "prismatic":
        effort = "100"
    elif joint_name in WHEELS:
        effort = "6.5"
    else:
        effort = "5"
    ET.SubElement(joint, "axis", xyz=numbers(source["axis"]))
    ET.SubElement(
        joint,
        "limit",
        lower=str(source["range"][0]),
        upper=str(source["range"][1]),
        effort=effort,
        velocity="50",
    )


def build_urdf():
    source = ASSETS / "source/chopped_urdf_v2"
    cad = ET.parse(source / "urdf/chopped_urdf_v2.urdf").getroot()
    ref = json.loads((ASSETS / "reference/training_dynamics.json").read_text())
    mapping = reference_mapping(ref)
    links = {e.get("name"): e for e in cad.findall("link")}
    frames = {"root": np.eye(4)}
    owners = {"root": "base"}
    pending = cad.findall("joint")
    wheel_owners = {
        f"{s}_wheel_{part}__{s}_wheel_{part}": s + "_wheel"
        for s in WHEELS
        for part in ("tire", "cap")
    }
    while pending:
        progress = False
        for j in pending[:]:
            parent, child = j.find("parent").get("link"), j.find("child").get("link")
            if parent not in frames:
                continue
            frames[child] = frames[parent] @ origin(j)
            name = j.get("name")
            # The CAD fixed chain mixes wheel and base parts.
            if name in ARM_JOINTS:
                owners[child] = name + "_link"
            elif "gripper" in name:
                owners[child] = owners[parent]
            elif child in wheel_owners:
                owners[child] = wheel_owners[child]
            else:
                inherited = owners[parent]
                owners[child] = "base" if inherited.endswith("_wheel") else inherited
            pending.remove(j)
            progress = True
        if not progress:
            raise ValueError("Disconnected or cyclic source URDF")

    # Align the CAD's floor-origin coordinates to the training axle origin.
    centers = []
    for side in WHEELS:
        link = f"{side}_wheel_tire__{side}_wheel_tire"
        v = links[link].find("visual")
        mesh = trimesh.load(source / "meshes" / Path(v.find("geometry/mesh").get("filename")).name)
        mesh.apply_transform(frames[link] @ origin(v))
        centers.append(mesh.bounds.mean(axis=0))
    alignment = np.eye(4)
    alignment[:3, 3] = -np.mean(centers, axis=0)
    ref_frames = {mapping[b["name"]]: transform(b["xpos"], b["xquat"]) for b in ref["bodies"]}
    robot = ET.Element("robot", name="chopped_urdf_v2_training_dynamics")
    extension = ET.SubElement(robot, "mujoco")
    ET.SubElement(
        extension,
        "compiler",
        discardvisual="false",
        fusestatic="false",
        meshdir="../source/chopped_urdf_v2/meshes",
        strippath="true",
    )
    output_links = {}
    inertial_rows = []
    for b in ref["bodies"]:
        name = mapping[b["name"]]
        link = ET.SubElement(robot, "link", name=name)
        output_links[name] = link
        inertial = ET.SubElement(link, "inertial")
        # Use link-axis tensors: MuJoCo 3.8.1 drops URDF inertial rotations.
        urdf_origin(inertial, transform(b["ipos"]))
        rotation = transform(quat=b["iquat"])[:3, :3]
        tensor = rotation @ np.diag(b["inertia"]) @ rotation.T
        ET.SubElement(inertial, "mass", value=str(b["mass"]))
        values = dict(
            ixx=tensor[0, 0],
            iyy=tensor[1, 1],
            izz=tensor[2, 2],
            ixy=tensor[0, 1],
            ixz=tensor[0, 2],
            iyz=tensor[1, 2],
        )
        ET.SubElement(inertial, "inertia", **{k: str(v) for k, v in values.items()})
        inertial_rows.append(
            dict(
                link=name,
                training_body=b["name"],
                mass_kg=b["mass"],
                com_x_m=b["ipos"][0],
                com_y_m=b["ipos"][1],
                com_z_m=b["ipos"][2],
                **values,
            )
        )
        if name != "base":
            add_urdf_joint(robot, b, mapping)
    visual_count = 0
    for name, link in links.items():
        owner = owners[name]
        for visual in link.findall("visual"):
            mesh = visual.find("geometry/mesh")
            if mesh is None:
                raise ValueError("Unexpected non-mesh CAD visual")
            # Keep the CAD zero pose while changing the attachment frame.
            t = np.linalg.inv(ref_frames[owner]) @ alignment @ frames[name] @ origin(visual)
            v = ET.SubElement(output_links[owner], "visual", name=name)
            urdf_origin(v, t)
            g = ET.SubElement(v, "geometry")
            ET.SubElement(
                g,
                "mesh",
                filename="../source/chopped_urdf_v2/meshes/" + Path(mesh.get("filename")).name,
                scale=mesh.get("scale", "1 1 1"),
            )
            material = ET.SubElement(v, "material", name=name + "_color")
            color = visual.find("material/color")
            ET.SubElement(
                material, "color", rgba=color.get("rgba") if color is not None else ".65 .65 .7 1"
            )
            visual_count += 1
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ET.indent(robot)
    ET.ElementTree(robot).write(
        MODEL_DIR / "chopped_policy.urdf", encoding="utf-8", xml_declaration=True
    )
    report = dict(
        total_mass_kg=sum(b["mass"] for b in ref["bodies"]),
        visual_count=visual_count,
        source_link_count=len(links),
        corrected_link_count=len(output_links),
        cad_to_axle=alignment.tolist(),
        reference_to_link=mapping,
        source_link_to_corrected_link=owners,
    )
    (MODEL_DIR / "conversion.json").write_text(json.dumps(report, indent=2) + "\n")
    with (MODEL_DIR / "inertials.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(inertial_rows[0]))
        writer.writeheader()
        writer.writerows(inertial_rows)
    return ref, mapping


def build_models():
    ref, mapping = build_urdf()
    for kind in ["arms", "terrain", "lean"]:
        spec = mujoco.MjSpec.from_file(str(MODEL_DIR / "chopped_policy.urdf"))
        spec.compiler.inertiafromgeom = 0
        spec.option.timestep = DT
        spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
        spec.option.iterations = 10
        spec.option.ls_iterations = 20
        spec.option.tolerance = 1e-8
        spec.option.ls_tolerance = 0.01
        base = spec.body("base")
        base.add_freejoint(name="floating_base")
        base.add_site(name="imu", size=[0.01, 0.01, 0.01])
        for c in ref["contacts"]:
            spec.body(mapping[c["body"]]).add_geom(
                name=c["name"],
                type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                pos=c["pos"],
                quat=c["quat"],
                size=c["size"],
                friction=c["friction"],
                contype=1,
                conaffinity=1,
                rgba=[0.2, 0.2, 0.2, 0],
                group=3,
            )
        for side in WHEELS:
            cfg = json.loads((ASSETS / f"reference/wheel_{side}.json").read_text())
            j = spec.joint(side)
            j.armature = round(cfg["i_wheel"] - 0.00357964, 6)
            j.frictionloss = cfg["frictionloss"]
            j.damping[0] = cfg["viscous"]
            a = spec.add_actuator(name=side, target=side, trntype=mujoco.mjtTrn.mjTRN_JOINT)
            a.set_to_motor()
            a.forcelimited = True
            a.forcerange = [-15, 15]
        for name in ARM_JOINTS:
            j = spec.joint(name)
            if kind != "arms":
                spec.delete(j)
                continue
            slide = name.endswith("j0")
            if slide:
                j.stiffness[0] = 0.1267
                j.springref = 100
            a = spec.add_actuator(name=name, target=name, trntype=mujoco.mjtTrn.mjTRN_JOINT)
            a.set_to_position(kp=59.26 if slide else 11.0, kv=40.0 if slide else 1.5)
            a.forcelimited = not slide
            if not slide:
                a.forcerange = [-5, 5]
        if kind == "lean":
            base.add_geom(
                name="chest",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=[0.0334, 0, 0.8217],
                size=[0.0100, 0.0382, 0.3200],
                contype=2,
                conaffinity=2,
                solref=[0.02, 1],
                rgba=[0.8, 0.2, 0.2, 0],
                group=3,
            )
            wall = spec.worldbody.add_body(name="table_edge", mocap=True, pos=[0.32, 0, 0.85])
            wall.add_geom(
                name="table_edge_geom",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=[0.05, 0.6, 0.15],
                mass=0,
                contype=2,
                conaffinity=2,
                rgba=[0.47, 0.31, 0.18, 1],
            )
        spec.worldbody.add_geom(
            name="floor",
            type=mujoco.mjtGeom.mjGEOM_PLANE,
            size=[0, 0, 0.05],
            contype=1,
            conaffinity=1,
            friction=[1, 0.005, 0.0001],
            rgba=[0.22, 0.26, 0.3, 1],
        )
        spec.worldbody.add_light(pos=[2, -2, 4], dir=[-0.4, 0.4, -1], diffuse=[0.8, 0.8, 0.8])
        spec.worldbody.add_light(pos=[-2, 2, 3], dir=[0.3, -0.3, -1], diffuse=[0.4, 0.4, 0.4])
        spec.meshdir = "../source/chopped_urdf_v2/meshes"
        xml = ET.fromstring(spec.to_xml())
        asset = xml.find("asset")
        ET.SubElement(
            asset,
            "texture",
            name="sky",
            type="skybox",
            builtin="gradient",
            rgb1=".35 .45 .60",
            rgb2=".08 .10 .15",
            width="512",
            height="3072",
        )
        ET.SubElement(
            asset,
            "texture",
            name="ground_pattern",
            type="2d",
            builtin="checker",
            rgb1=".25 .28 .31",
            rgb2=".32 .35 .38",
            width="512",
            height="512",
        )
        ET.SubElement(
            asset,
            "material",
            name="ground_material",
            texture="ground_pattern",
            texrepeat="2 2",
            texuniform="true",
            reflectance=".08",
        )
        floor = xml.find(".//geom[@name='floor']")
        floor.set("material", "ground_material")
        floor.set("rgba", "1 1 1 1")
        # Restore precision lost by MuJoCo's six-digit XML serialization.
        for b in ref["bodies"]:
            body = xml.find(f".//body[@name='{mapping[b['name']]}']")
            body.set("pos", numbers(b["pos"]))
            body.set("quat", numbers(b["quat"]))
            inertial = body.find("inertial")
            inertial.attrib.clear()
            inertial.attrib.update(
                pos=numbers(b["ipos"]),
                quat=numbers(b["iquat"]),
                mass=str(b["mass"]),
                diaginertia=numbers(b["inertia"]),
            )
            for j in b["joints"]:
                if j["type"] == 0:
                    continue
                name = (
                    j["name"] if j["name"] in WHEELS else mapping[b["name"]].removesuffix("_link")
                )
                joint = body.find(f"joint[@name='{name}']")
                if joint is not None:
                    joint.set("axis", numbers(j["axis"]))
                    joint.set("pos", numbers(j["pos"]))
                    if j["limited"]:
                        joint.set("range", numbers(j["range"]))
        for c in ref["contacts"]:
            geom = xml.find(f".//geom[@name='{c['name']}']")
            geom.set("pos", numbers(c["pos"]))
        ET.indent(xml)
        ET.ElementTree(xml).write(MODEL_DIR / f"{kind}.xml", encoding="utf-8", xml_declaration=True)
        model = mujoco.MjModel.from_xml_path(str(MODEL_DIR / f"{kind}.xml"))
        print(
            f"{kind}: {model.nbody - 1} bodies, {model.njnt} joints, {model.nu} actuators, "
            f"{model.body_mass.sum():.6f} kg"
        )


if __name__ == "__main__":
    build_models()
