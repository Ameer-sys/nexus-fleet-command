"""Build the native IK runtime from a bracketbot_ik checkout without editing it."""

import argparse
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    suffix = ".dylib" if platform.system() == "Darwin" else ".so"
    destination = Path(__file__).resolve().parents[1] / "assets" / "ik"
    destination /= f"{platform.system().lower()}-{platform.machine()}"
    with tempfile.TemporaryDirectory(prefix="bb-ik-") as directory:
        root = Path(directory)
        for crate in ("hybrid_ik", "qp_ik", "ranged_ik"):
            source, target = args.source / crate, root / crate
            shutil.copytree(source / "src", target / "src")
            for name in ("Cargo.toml", "Cargo.lock"):
                if (source / name).exists():
                    shutil.copy2(source / name, target / name)
        wrapper = root / "hybrid_ik/src/wrapper.rs"
        code = wrapper.read_text()
        code = code.replace("    joint_centering_gain: c_double,\n", "")
        code = code.replace("        joint_centering_gain,\n", "        10.0,\n")
        start = code.index("fn vec_to_opt(")
        end = code.index("\n// ----", start)
        code = (
            code[:start]
            + """fn vec_to_opt(v: Vec<f64>) -> Opt {
    let values = v.into_boxed_slice();
    let length = values.len() as c_int;
    Opt { data: Box::into_raw(values) as *const c_double, length }
}

#[no_mangle]
pub unsafe extern "C" fn free_result(result: Opt) {
    if !result.data.is_null() {
        let slice = std::ptr::slice_from_raw_parts_mut(
            result.data as *mut c_double, result.length as usize);
        drop(Box::from_raw(slice));
    }
}
"""
            + code[end:]
        )
        wrapper.write_text(code)
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--locked",
                "--manifest-path",
                str(root / "hybrid_ik/Cargo.toml"),
            ],
            check=True,
        )
        destination.mkdir(parents=True, exist_ok=True)
        library = destination / f"libhybrid_ik_lib{suffix}"
        shutil.copy2(root / f"hybrid_ik/target/release/libhybrid_ik_lib{suffix}", library)
        if platform.system() == "Darwin":
            subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{library.name}", str(library)],
                check=True,
            )
            subprocess.run(["codesign", "--force", "--sign", "-", str(library)], check=True)
    print(destination / f"libhybrid_ik_lib{suffix}")


if __name__ == "__main__":
    main()
