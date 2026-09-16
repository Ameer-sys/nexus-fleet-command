# Simulation details

Run commands from the repository root.

## Model to inspect

- [Corrected URDF](../assets/chopped_policy/chopped_policy.urdf): 21 physical links with wheel hinges and 14 arm joints. Grippers are fixed, as in the arm-motion training task.
- [Mass/COM/inertia table](../assets/chopped_policy/inertials.csv): mass in kg, COM in metres, full inertia about COM in each link's axes in kg·m².
- [Link mapping and CAD alignment](../assets/chopped_policy/conversion.json).
- [Original chopped export](../assets/source/chopped_urdf_v2/urdf/chopped_urdf_v2.urdf), preserved for comparison.
- [Numeric training reference](../assets/reference/training_dynamics.json), including independent kinematics samples exported on the training host.

The original chopped file had placeholder inertias and fixed wheels. The corrected file groups its 50 visual meshes onto the corresponding physical links, restores the training joint origins/axes, and transfers each body's complete mass, COM and inertia. It retains the chopped model's zero-pose visual assembly, translated from its floor origin to the wheel-axle origin. The total robot mass is **13.605122 kg**: base 6.931796 kg, wheels 1.132605 kg each, arms including fixed fingers 4.408116 kg total.

The joint frame changes are intentional: copying COM numbers between different CAD frames would give the wrong physical model when an arm moves. The original chopped shapes are retained; they are visual approximations to the training CAD and can differ around joint pivots. The dynamics use the training frames and tensors.

The three small generated scene files (`arms.xml`, `terrain.xml`, `lean.xml`) load **only chopped STL meshes**. They add the free joint, training wheel cylinders, actuators, lighting and ground; lean also adds the training mast contact box and a table-edge slab. They do not load the complex `bb_v2.xml` or any of its meshes at runtime or during local rebuilding.

Rebuild after changing the converter:

```bash
uv run --locked python -m bbsim.model
```

## Reproduced behavior and limits

- Policies run at **200 Hz**, with signed wheel observations and torque commands, oldest-to-newest gyro/action histories, and the policy-specific input layouts.
- The arm policy samples all 14 arm encoders at **50 Hz**, holding the packet between samples and retaining three velocity frames. Rotary servos use kp=11, kv=1.5 and ±5 N·m; slides use kp=59.26, kv=40 and the training support spring.
- Wheels use the separate learned left/right residual LSTMs, DC torque-speed envelopes, stuck-wheel gate, joint armature, friction and damping. The NumPy recurrences are checked against PyTorch outputs exported on the training host.
- The default wheel observations use the training Hall-edge 1/T estimator. Pitch and gyro use clean simulated orientation/rates. ARC integrates commanded minus true body-frame forward velocity, as in training. This differs from the STM's wheel-odometry approximation.
- This is deterministic nominal playback: no randomized mass/COM changes, observation noise, transport delays, acceleration-leakage perturbations or low-speed random motor noise. It does not emulate the STM IMU/Madgwick implementation or the original training distribution.
- Visual arm/head meshes do not collide, matching the training collision setup. The lean mast contacts only the table slab. Contact transients and successful short rollouts are not hardware validation.
- The reference is the nominal model loaded by `bb-arms-v3`; training startup domain randomization could subsequently alter mass/COM/inertia. We deliberately preserve the reference values for inspection.

## Sources and verification

See [PROVENANCE.md](../PROVENANCE.md) for source paths, artifact identities, the lean reconstruction, and inertia-import details. [Validation results](../validation/results.json) record the checked rollouts and numerical parity. Tests compare body positions, COM and inertia against **16 articulated poses exported independently on the training host**, rather than just checking the converter against itself.

Implementation is split into `bbsim/model.py` (asset conversion), `policy.py` (inference, observations, motors), `simulation.py` (physics loop), `drive.py` (held-input controls), `drive_ui.py` (native window), and `cli.py` (launch options). Shared paths and policy constants live in `config.py`. The `tools/` scripts regenerate source exports and reference fixtures; normal playback is fully offline.

For development checks:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m pytest -q
```
