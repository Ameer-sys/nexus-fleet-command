"""Compile the firmware's C inference code to generate lean reference outputs."""

import argparse
import ctypes
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "assets/reference"


def export_reference(source: Path, destination: Path = REFERENCE_DIR):
    text = source.read_text()
    functions = text[text.index("static inline float elu(") : text.index("void policy_forward(")]
    lean = text[text.index("void policy_forward_lean(") :]
    standalone = (
        '#include <math.h>\n#include <stddef.h>\n#include "lean_policy_export.h"\n'
        "#define POLICY_N_H1 256\n#define POLICY_N_H2 128\n#define POLICY_N_OUT 4\n"
        "typedef void (*policy_service_fn)(void);\n" + functions + lean
    )
    cfile = destination / "lean_reference.c"
    cfile.write_text(standalone)
    with tempfile.TemporaryDirectory() as tmp:
        library = Path(tmp) / "lean_reference.so"
        subprocess.run(
            ["cc", "-O2", "-shared", "-fPIC", "-o", str(library), str(cfile)], check=True
        )
        lib = ctypes.CDLL(str(library))
        ptr = np.ctypeslib.ndpointer(dtype=np.float32, flags="C_CONTIGUOUS")
        lib.policy_forward_lean.argtypes = [ptr, ptr, ctypes.c_void_p]
        rng = np.random.default_rng(1919)
        obs = rng.normal(0, 0.5, (2048, 19)).astype(np.float32)
        obs[:, 18] = rng.uniform(1 / 3, 5, len(obs))
        actions = np.zeros((len(obs), 2), np.float32)
        for x, y in zip(obs, actions):
            lib.policy_forward_lean(x, y, None)
        np.savez_compressed(destination / "lean_c_parity.npz", obs=obs, actions=actions)
    info = dict(
        source=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        c_reference_sha256=hashlib.sha256(cfile.read_bytes()).hexdigest(),
        samples=len(obs),
        description="Verbatim ELU/Softplus/linear/forward/lean functions from STM policy.c; unrelated arms weights and initialization removed.",
    )
    (destination / "lean_c_parity.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, help="STM balance/policy.c; requires a local C compiler"
    )
    args = parser.parse_args()
    print(json.dumps(export_reference(args.source), indent=2))


if __name__ == "__main__":
    main()
