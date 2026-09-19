"""Launch the complete NEXUS Control Center with one command."""

from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from nexus.server import ControlCenterRuntime, create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NEXUS live warehouse dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo-speed",
        type=float,
        default=0.65,
        help="Simulation speed relative to the 200 Hz physics clock (default: 0.65)",
    )
    args = parser.parse_args()
    runtime = ControlCenterRuntime(demo_speed=args.demo_speed)
    chain = runtime.ensure_demo().custody_ledger.get_status()
    print(f"[SOLANA] transport={chain['transport']}")
    print(f"[SOLANA] cluster={chain['cluster']}")
    if chain["enabled"]:
        wallet = chain["wallet"]
        print(f"[SOLANA] wallet={wallet[:4]}...{wallet[-4:]}")
        print(f"[SOLANA] balance={chain['balance']} SOL")
        print("[SOLANA] custody ledger ready")
    else:
        print(f"[SOLANA] offline: {chain['message']}")
    app = create_app(runtime)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
