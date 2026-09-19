# NEXUS — Autonomous Fleet Intelligence

NEXUS is a Hack the North warehouse-fleet control system built on the official
[Bracket Bot simulator](https://github.com/Bracket-Bot-Inc/bb-sim). It combines
multi-robot job auctions, priority scheduling, traffic coordination, failure
recovery, a live command dashboard, and optional Solana Devnet custody
attestations. Everything runs in simulation; no physical robot is required.

## Setup

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/Ameer-sys/nexus-fleet-command.git
cd nexus-fleet-command
uv sync --locked
```

uv installs Python 3.12 and the locked dependencies automatically when needed.

## NEXUS Control Center

Launch the live autonomous warehouse dashboard with one command:

```sh
uv run python scripts/run_nexus_control_center.py
```

Open `http://127.0.0.1:8000`, then select **Start Demo**. The default
0.65x pacing makes the deterministic traffic, priority-job, failure, and
recovery story run for about one minute. Use `--demo-speed 1.0` for real-time
physics pacing or a larger value for faster testing.

The dashboard opens in **Fleet Command** mode. Select one of the 16 packages
packages on the warehouse map, select a destination zone, adjust handling and
priority, then choose **Dispatch Fleet**. NEXUS runs its real auction and shows
the distance, battery, health, reliability, traffic, and workload costs behind
the selected robot. Use **Simulate Robot Failure** during a delivery to watch
the existing recovery and reauction path. **Scripted Demo** remains available
in the top command bar as the deterministic backup presentation.

Fleet Command accepts additional packages while robots are moving. Three jobs
can execute concurrently; further jobs stay visibly queued in scheduler
priority order and dispatch automatically when an agent becomes free. Use
**Load Traffic Test** after a reset to submit real priority-3 west-to-east and
priority-10 east-to-west jobs, demonstrating conflict prediction, priority
right-of-way, yielding, and resumption without synthetic traffic events.

### Solana Devnet custody attestations

NEXUS records the seven meaningful custody transitions for
`MEDICAL-CRITICAL` as signed Solana Devnet Memo transactions. Writes run on a
background queue, so a missing wallet or RPC outage never pauses the robots.
The integration uses the wallet already configured by the Solana CLI and does
not read or store secret-key contents.

```powershell
solana config set --url devnet
solana balance
$env:NEXUS_SOLANA_ENABLED = "1"
$env:NEXUS_SOLANA_CLUSTER = "devnet"
$env:NEXUS_SOLANA_TRANSPORT = "auto" # native CLI, then WSL CLI
uv run python scripts/run_nexus_control_center.py
```

Set `NEXUS_SOLANA_TRANSPORT` to `native` or `wsl` to force either CLI mode.

If the displayed Devnet balance is too low, fund the development wallet
manually with `wsl.exe solana airdrop 2 --url devnet` on Windows, or
`solana airdrop 2 --url devnet` when using a native CLI. Devnet SOL has no real value.
Without the CLI, wallet, balance, or network, the dashboard reports
`OFFLINE / DEMO CONTINUES` and the complete deterministic demo still runs.

## Tests

```sh
uv run pytest
```

The test suite covers fleet allocation, navigation, traffic coordination,
failure recovery, the HTTP control API, warehouse state, and native/WSL Solana
transport behavior.

## Attribution

NEXUS preserves the complete history of the upstream
[Bracket-Bot-Inc/bb-sim](https://github.com/Bracket-Bot-Inc/bb-sim) project.
The original simulator, models, and weights belong to their respective owners.
No new license is asserted over upstream material.

## Run

Run one of these to open the drive UI:

```sh
uv run --locked python -m bbsim arms
uv run --locked python -m bbsim terrain
uv run --locked python -m bbsim lean
```

Hold **WASD** to drive; release to stop. **Space** stops, **R** resets, **P** pauses, **Esc** closes. **M** toggles arm motion; **L** toggles table lean. Drag/scroll to move the camera.

Tested on Apple Silicon macOS; the UI requires a desktop with OpenGL. For a headless check, append `--headless --duration 5`. Use `--help` for options.

## Manipulation

```sh
uv run --locked python -m bbsim.manipulation
```
