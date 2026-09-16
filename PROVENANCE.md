# Source provenance

Captured from `bracketbot@100.73.185.62` (`bb-local-1`) on 2026-09-16. Training files were read/exported; the training repository was not edited.

## Robot

- Requested training checkout: `/home/bracketbot/bb_RL/bb_mjlab`, HEAD `b06d70cdc233de71471b31af074c7b4c66456278`. It has local modifications, so commit identity alone is insufficient.
- Robot source: `bb-urdf/mjcf/bb_v2.xml`, SHA-256 `ff13f98f2686bcbdaa5cf1c25242dd4cb41fa529232c9e643b245cfa3daab4b9`.
- The exact same robot XML hash exists in the frozen arm training campaign at `/home/bracketbot/bb_RL/bb_arm_motion_campaign_20260908`.
- Numeric export uses that campaign's `bb_mjlab.tasks.arms_v3_twist_arms_motion.robot_cfg.get_motion_spec()`: the actual model builder recorded in the deployed checkpoint's `env.yaml`. It adds the floating base and wheel contacts, fixes the fingers, and retains the 14 articulated arm joints.
- `tools/export_training.py` exports the compiled masses, inertial frames, joint axes/origins/ranges, wheel contacts, 16 reference poses, learned motor weights and PyTorch motor parity samples. This captures the effective loaded asset despite the dirty training checkout.
- Chopped visual source: `/Users/jliu/Downloads/chopped_urdf_v2 (1).zip`. Its URDF and STL meshes are preserved under `assets/source`. The Downloads archive is unchanged.

The generated URDF stores each full inertia tensor in link axes at the COM with inertial `rpy=0`. This avoids a reproduced MuJoCo 3.8.1 behavior that discards nonzero inertial-origin rotation when compiling URDF. The same issue is described in [MuJoCo issue 3559](https://github.com/google-deepmind/mujoco/issues/3559). The full tensors stored in the URDF match the numeric reference to floating-point precision. MuJoCo's subsequent URDF eigen-decomposition reconstructs tensors within 1e-6 kg·m²; the generated MJCF keeps the reference principal axes directly and is checked within 1e-12 kg·m². The generator also restores full precision after MuJoCo's XML serializer, which otherwise rounds numeric fields.

## Policies

| Policy | Source | Identity |
| --- | --- | --- |
| Arms | `deploy_history5049_0152_20260908/stage/models/arms_motion_history_5049.onnx` | ONNX SHA-256 `f058a96e3563048c3d7b27eb448811d4c1214311bc03056566d08dfed7d5dc38` |
| Terrain | `deploy_history5049_0152_20260908/stage/models/balancing_terrain.onnx` | SHA-256 `ea9bc8ec6c94b34d9eade03e2435f47bf515ed19857555e961f5668f8c475165`; identical to `FAST2475_TERRAIN_HOLD03` in bbcore's `17a04d2` firmware export |
| Lean | Deployed `bb-bedrock/bb-firmware/stm/balance/gen/policy_weights_lean.h` | `LEAN_V3E`; original ONNX SHA recorded by the export: `198609acb21b137d0f5f74e92f1a951c82346da25a250d6199ad8204399c27ba` |

The original LEAN_V3E ONNX was not found in the available training checkout. `tools/import_policies.py` reconstructs an ONNX graph from the deployed float32 C weights, with the same ELU layers and Beta-mean output. Its six weight arrays are bit-identical to the C export; the **ONNX file itself has a new hash**. `assets/policies/provenance.json` records both identities. `tools/lean_reference.py` extracts the actual firmware inference functions and compiles a reference library to create 2,048 independent parity cases. C and ONNX floating-point reduction/activation rounding differ by about 1e-5 in the stress corpus; this is not retraining.

Lean observation 18 is **requested degrees / 3**, not the binary mode flag used by newer lean tasks in the current repository. Using the current task's layout would silently run the wrong policy contract. The saved STM definition is authoritative for this artifact.

The 74-input arm actor is checked against all 5,269 saved deployment parity cases. Its checkpoint SHA-256 is `03a4b15ada18c6d0c633373ed27a39e144a19c8691e442a9d21b5172a8e5e99b`. Metadata and the saved environment configuration are bundled. The policies output two normalized actions; wheel torque requests are `[-6.5, +6.5] * actions` before motor dynamics.

## Re-exporting

On the training host, from the frozen campaign directory:

```bash
PYTHONPATH=src ../bb_mjlab/.venv/bin/python /path/to/export_training.py /tmp/bb-sim-export
```

Copy those numeric exports into `assets/reference`, then rebuild locally with `.venv/bin/python -m bbsim.model`. No complex training mesh is needed after the export. The archived `lean_policy_export.h`, independent C fixture and source hashes permit checking the recovered lean policy later.
