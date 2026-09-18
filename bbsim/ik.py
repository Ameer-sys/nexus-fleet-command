"""The arm daemon's native IK interface, using kinematics from the active MJCF."""

import ctypes as ct
import platform
from pathlib import Path
from xml.etree import ElementTree as ET

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation

from .config import ASSETS

DoublePtr = ct.POINTER(ct.c_double)


class Result(ct.Structure):
    _fields_ = [("data", DoublePtr), ("length", ct.c_int)]


def vector(values):
    return " ".join(f"{x:.16g}" for x in values)


def kinematic_urdf(model, side):
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    robot = ET.Element("robot", name=f"{side}_arm")
    parent = "arm_base"
    ET.SubElement(robot, "link", name=parent)
    base = model.body("base").id
    position, rotation = data.xpos[base].copy(), data.xmat[base].reshape(3, 3).copy()
    for index in range(8):
        name = f"{side}j{index}" if index < 7 else f"{side}_eef"
        if index < 7:
            joint = model.joint(name)
            anchor = data.xanchor[joint.id]
            axis = data.xaxis[joint.id]
            helper = np.eye(3)[np.argmin(np.abs(axis))]
            x = np.cross(helper, axis)
            x /= np.linalg.norm(x)
            frame = np.column_stack((x, np.cross(axis, x), axis))
            kind = "prismatic" if index == 0 else "revolute"
        else:
            site = model.site(name).id
            anchor = data.site_xpos[site]
            frame = data.site_xmat[site].reshape(3, 3)
            kind = "fixed"
        ET.SubElement(robot, "link", name=name)
        element = ET.SubElement(robot, "joint", name=f"{name}_joint", type=kind)
        ET.SubElement(element, "parent", link=parent)
        ET.SubElement(element, "child", link=name)
        ET.SubElement(
            element,
            "origin",
            xyz=vector(rotation.T @ (anchor - position)),
            rpy=vector(Rotation.from_matrix(rotation.T @ frame).as_euler("xyz")),
        )
        if index < 7:
            ET.SubElement(element, "axis", xyz="0 0 1")
            ET.SubElement(
                element,
                "limit",
                lower=str(joint.range[0]),
                upper=str(joint.range[1]),
                effort="100",
                velocity="2",
            )
        parent, position, rotation = name, anchor, frame
    return ET.tostring(robot)


class NativeIK:
    def __init__(self, model, side, library=None, api="daemon"):
        suffix = ".dylib" if platform.system() == "Darwin" else ".so"
        path = (
            Path(library)
            if library
            else (
                ASSETS
                / "ik"
                / f"{platform.system().lower()}-{platform.machine()}"
                / f"libhybrid_ik_lib{suffix}"
            )
        )
        try:
            self.lib = ct.CDLL(str(path.resolve()))
        except OSError as error:
            raise RuntimeError(
                f"Cannot load IK library {path}: {error}\n"
                "Supply --ik-library built for this OS/CPU, or run "
                "uv run python scripts/build_ik.py /path/to/bracketbot_ik"
            ) from error
        lib = self.lib
        lib.relaxed_ik_new.argtypes = [ct.c_char_p] * 3 + [DoublePtr, ct.c_int] * 2
        lib.relaxed_ik_new.argtypes += [ct.c_double] * (2 if api == "legacy" else 1) + [ct.c_int]
        lib.relaxed_ik_new.restype = ct.c_void_p
        lib.relaxed_ik_free.argtypes = [ct.c_void_p]
        lib.relaxed_ik_free.restype = None
        for name in ("reset", "set_tolerances", "set_centering_weights"):
            function = getattr(lib, name)
            function.argtypes = [ct.c_void_p, DoublePtr, ct.c_int]
            function.restype = None
        lib.solve_pose.argtypes = [
            ct.c_void_p,
            DoublePtr,
            ct.c_int,
            DoublePtr,
            ct.c_int,
        ]
        lib.solve_pose.restype = Result
        lib.forward_kinematics.argtypes = [ct.c_void_p, DoublePtr, ct.c_int]
        lib.forward_kinematics.restype = Result
        self.release = getattr(lib, "free_result", None)
        if self.release:
            self.release.argtypes, self.release.restype = [Result], None
        else:
            # bbcore's Vec<f64> results use Rust's default system allocator.
            self.free = ct.CDLL(None).free
            self.free.argtypes, self.free.restype = [ct.c_void_p], None
        initial = self._array(np.zeros(7))
        weights = [10.0, 0.1] if api == "legacy" else [0.1]
        self.ptr = lib.relaxed_ik_new(
            kinematic_urdf(model, side),
            b"arm_base",
            f"{side}_eef".encode(),
            initial,
            7,
            initial,
            7,
            *weights,
            5,
        )
        if not self.ptr:
            raise RuntimeError("IK constructor returned a null solver")
        lib.set_tolerances(self.ptr, self._array([0.001] * 3 + [0.01] * 3), 6)
        lib.set_centering_weights(self.ptr, self._array([50, 25, 15, 5, 10, 12, 1.5]), 7)

    @staticmethod
    def _array(values):
        return (ct.c_double * len(values))(*values)

    def _result(self, result):
        try:
            if result.length != 7 or not result.data:
                raise RuntimeError("IK returned an invalid result")
            value = np.ctypeslib.as_array(result.data, shape=(7,)).copy()
            if not np.isfinite(value).all():
                raise RuntimeError("IK returned non-finite values")
            return value
        finally:
            if self.release:
                self.release(result)
            elif result.data:
                self.free(result.data)

    def solve(self, position, quaternion):
        return self._result(
            self.lib.solve_pose(
                self.ptr,
                self._array(position),
                3,
                self._array(quaternion),
                4,
            )
        )

    def forward(self, joints):
        return self._result(self.lib.forward_kinematics(self.ptr, self._array(joints), 7))

    def reset(self, joints):
        self.lib.reset(self.ptr, self._array(joints), 7)

    def close(self):
        if self.ptr:
            self.lib.relaxed_ik_free(self.ptr)
            self.ptr = None
