# bb-sim

Run the arms, terrain, and lean policies in MuJoCo with the chopped robot model. Models and weights are included; no robot connection is needed.

## Setup

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/Bracket-Bot-Inc/bb-sim.git
cd bb-sim
uv sync --locked
```

uv installs Python 3.12 and the locked dependencies automatically when needed.

## Run

Run one of these to open the drive UI:

```sh
uv run --locked python -m bbsim arms
uv run --locked python -m bbsim terrain
uv run --locked python -m bbsim lean
```

Hold **WASD** to drive; release to stop. **Space** stops, **R** resets, **P** pauses, **Esc** closes. **M** toggles arm motion; **L** toggles table lean. Drag/scroll to move the camera.

Tested on Apple Silicon macOS; the UI requires a desktop with OpenGL. For a headless check, append `--headless --duration 5`. Use `--help` for options. [Model details](docs/simulation.md) · [Provenance](PROVENANCE.md).
