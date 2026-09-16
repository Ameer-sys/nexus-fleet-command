"""Import recovered policy artifacts; rebuild LEAN_V3E from its deployed C export."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto
from onnx import helper as h
from onnx import numpy_helper as nh


def import_policies(stage: Path, lean_header: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    provenance = {}
    for kind, name in [("arms", "arms_motion_history_5049"), ("terrain", "balancing_terrain")]:
        source = stage / (name + ".onnx")
        shutil.copy2(source, output / (kind + ".onnx"))
        provenance[kind] = dict(
            source=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest()
        )
    shutil.copy2(stage / "arms_motion_history_5049.json", output / "arms_metadata.json")
    shutil.copy2(stage / "arms_motion_history_5049.parity.npz", output / "arms_parity.npz")
    text = lean_header.read_text()
    arrays = {}
    for name, count, values in re.findall(
        r"static const float (LEAN_\w+)\[(\d+)\] = \{(.*?)\};", text, re.S
    ):
        array = np.array(
            [float(v.strip().removesuffix("f")) for v in values.split(",") if v.strip()],
            dtype=np.float32,
        )
        assert len(array) == int(count)
        arrays[name.removeprefix("LEAN_")] = array
    nodes, initializers = [], []
    previous = "obs"
    for i, (n_in, n_out) in enumerate([(19, 256), (256, 128), (128, 4)]):
        initializers.extend(
            [
                nh.from_array(arrays[f"W{i}"].reshape(n_out, n_in).T.copy(), f"W{i}"),
                nh.from_array(arrays[f"B{i}"], f"B{i}"),
            ]
        )
        nodes.extend(
            [
                h.make_node("MatMul", [previous, f"W{i}"], [f"mm{i}"]),
                h.make_node("Add", [f"mm{i}", f"B{i}"], [f"z{i}"]),
            ]
        )
        previous = f"z{i}"
        if i < 2:
            nodes.append(h.make_node("Elu", [previous], [f"h{i}"], alpha=1.0))
            previous = f"h{i}"
    initializers += [
        nh.from_array(np.array(1, dtype=np.float32), "one"),
        nh.from_array(np.array(2, dtype=np.float32), "two"),
        nh.from_array(np.array([0, 1], dtype=np.int64), "alpha_indices"),
        nh.from_array(np.array([2, 3], dtype=np.int64), "beta_indices"),
    ]
    nodes += [
        h.make_node("Softplus", ["z2"], ["sp"]),
        h.make_node("Add", ["sp", "one"], ["ab"]),
        h.make_node("Gather", ["ab", "alpha_indices"], ["alpha"], axis=1),
        h.make_node("Gather", ["ab", "beta_indices"], ["beta"], axis=1),
        h.make_node("Add", ["alpha", "beta"], ["sum"]),
        h.make_node("Div", ["alpha", "sum"], ["mean"]),
        h.make_node("Mul", ["mean", "two"], ["scaled"]),
        h.make_node("Sub", ["scaled", "one"], ["actions"]),
    ]
    graph = h.make_graph(
        nodes,
        "LEAN_V3E_from_deployed_float32_export",
        [h.make_tensor_value_info("obs", TensorProto.FLOAT, [1, 19])],
        [h.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 2])],
        initializers,
    )
    model = h.make_model(graph, opset_imports=[h.make_opsetid("", 17)], ir_version=9)
    onnx.checker.check_model(model)
    onnx.save(model, output / "lean.onnx")
    np.savez(output / "lean_weights.npz", **arrays)
    provenance["lean"] = dict(
        source=str(lean_header),
        source_header_sha256=hashlib.sha256(lean_header.read_bytes()).hexdigest(),
        original_onnx_sha256="198609acb21b137d0f5f74e92f1a951c82346da25a250d6199ad8204399c27ba",
        sha256=hashlib.sha256((output / "lean.onnx").read_bytes()).hexdigest(),
        note="Original ONNX unavailable in training checkout. Reconstructed identical float32 weights and Beta mean from deployed LEAN_V3E C header; graph bytes differ.",
    )
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stage", type=Path)
    p.add_argument("lean_header", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    import_policies(a.stage, a.lean_header, a.output)
