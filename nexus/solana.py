"""Asynchronous Solana Devnet custody attestations for NEXUS.

The robotics stack only talks to :class:`SolanaCustodyLedger`.  The default
transport shells out to the user's existing Solana CLI wallet and never reads
or copies private-key material into NEXUS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Protocol


DEVNET_RPC = "https://api.devnet.solana.com"
EXPLORER_TX = "https://explorer.solana.com/tx/{signature}?cluster=devnet"


class ChainStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class SolanaTransport(Protocol):
    wallet_address: str | None
    balance: float | None
    transport_mode: str

    def submit(self, memo: str) -> str: ...


@dataclass
class CustodyAttestation:
    sequence: int
    payload: dict[str, Any]
    memo: str
    chain_status: ChainStatus
    transaction_signature: str | None = None
    explorer_url: str | None = None
    error: str | None = None
    attempts: int = 0

    def telemetry(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            **self.payload,
            "memo": self.memo,
            "chain_status": self.chain_status.value,
            "transaction_signature": self.transaction_signature,
            "explorer_url": self.explorer_url,
            "error": self.error,
            "attempts": self.attempts,
        }


class SolanaCliTransport:
    """Submit signed zero-SOL self-transfers carrying an SPL Memo instruction."""

    def __init__(self, command_prefix: list[str], transport_mode: str) -> None:
        self.command_prefix = list(command_prefix)
        self.transport_mode = transport_mode
        self.version = self._run("--version", include_devnet=False).strip()
        self.config = self._run("config", "get", include_devnet=False).strip()
        self.wallet_address = self._run("address").strip()
        balance_output = self._run("balance").strip()
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", balance_output)
        self.balance = float(match.group(1)) if match else None
        help_result = subprocess.run(
            [*self.command_prefix, "transfer", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
        if "--with-memo" not in help_result.stdout:
            raise RuntimeError("installed Solana CLI does not support transfer --with-memo")

    def build_command(
        self,
        command: str,
        *args: str,
        include_devnet: bool = True,
    ) -> list[str]:
        built = [*self.command_prefix, command, *args]
        if include_devnet:
            built.extend(("--url", "devnet"))
        return built

    def _run(
        self,
        command: str,
        *args: str,
        timeout: float = 20,
        include_devnet: bool = True,
    ) -> str:
        result = subprocess.run(
            self.build_command(command, *args, include_devnet=include_devnet),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        if result.returncode:
            message = (result.stderr or result.stdout).strip()
            raise RuntimeError(message or f"solana {command} failed")
        return result.stdout

    def submit(self, memo: str) -> str:
        if not self.wallet_address:
            raise RuntimeError("Solana wallet address is unavailable")
        output = self._run(
            "transfer",
            self.wallet_address,
            "0",
            "--with-memo",
            memo,
            timeout=45,
        )
        matches = re.findall(r"[1-9A-HJ-NP-Za-km-z]{80,90}", output)
        if not matches:
            raise RuntimeError(f"Solana CLI returned no transaction signature: {output.strip()}")
        signature = matches[-1]
        self._run("confirm", signature, timeout=45)
        return signature

    @staticmethod
    def _probe(command_prefix: list[str]) -> bool:
        try:
            result = subprocess.run(
                [*command_prefix, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @classmethod
    def _wsl_prefix(cls, executable: str) -> list[str] | None:
        # --exec bypasses WSL's implicit shell parsing, preserving a JSON memo
        # as one argv value across the Windows/Linux boundary.
        direct = [executable, "--exec", "solana"]
        if cls._probe(direct):
            return direct
        # Some WSL installations add Solana to PATH only in a login shell.
        # Discover it once, then invoke the absolute binary directly thereafter.
        try:
            result = subprocess.run(
                [executable, "bash", "-lic", "command -v solana"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
            path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            if result.returncode == 0 and path.startswith("/"):
                absolute = [executable, "--exec", path]
                if cls._probe(absolute):
                    return absolute
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    @classmethod
    def discover(cls) -> tuple[SolanaCliTransport | None, str]:
        preference = os.getenv("NEXUS_SOLANA_TRANSPORT", "auto").strip().lower()
        if preference not in {"auto", "native", "wsl"}:
            return None, "NEXUS_SOLANA_TRANSPORT must be auto, native, or wsl"

        candidates: list[tuple[list[str], str]] = []
        if preference in {"auto", "native"}:
            executable = shutil.which("solana")
            if executable and cls._probe([executable]):
                candidates.append(([executable], "NATIVE_CLI"))
        if not candidates and preference in {"auto", "wsl"}:
            wsl = shutil.which("wsl.exe") or shutil.which("wsl")
            if wsl:
                prefix = cls._wsl_prefix(wsl)
                if prefix is not None:
                    candidates.append((prefix, "WSL_CLI"))

        if not candidates:
            return None, "No usable native or WSL Solana CLI found; demo continues"
        try:
            transport = cls(*candidates[0])
            if transport.balance is not None and transport.balance < 0.001:
                command = "wsl.exe solana" if transport.transport_mode == "WSL_CLI" else "solana"
                return transport, f"Devnet balance is low; run `{command} airdrop 2 --url devnet`"
            return transport, "ready"
        except Exception as error:
            return None, str(error)


class SolanaCustodyLedger:
    """Threaded, failure-isolated queue for meaningful custody events."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        cluster: str = "devnet",
        transport: SolanaTransport | None = None,
        max_attempts: int = 3,
    ) -> None:
        requested = (
            os.getenv("NEXUS_SOLANA_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        configured_cluster = os.getenv("NEXUS_SOLANA_CLUSTER", cluster).lower()
        self.cluster = "devnet"
        self.requested = requested
        self.max_attempts = max(1, int(max_attempts))
        self._records: list[CustodyAttestation] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[CustodyAttestation | None] = queue.Queue()
        self._closed = False
        self.message = "Solana disabled; demo continues"

        if configured_cluster != "devnet":
            requested = False
            self.message = "Only Solana Devnet is permitted; demo continues"
        if requested and transport is None:
            transport, self.message = SolanaCliTransport.discover()
        self.transport = transport if requested else None
        self.enabled = self.transport is not None
        self.transport_mode = getattr(self.transport, "transport_mode", "UNAVAILABLE")
        self.wallet_address = getattr(self.transport, "wallet_address", None)
        self.balance = getattr(self.transport, "balance", None)
        if self.enabled and self.message == "Solana disabled; demo continues":
            self.message = "ready"

        self._worker: threading.Thread | None = None
        if self.enabled:
            self._worker = threading.Thread(
                target=self._work,
                name="nexus-solana-custody",
                daemon=True,
            )
            self._worker.start()

    @staticmethod
    def _canonical_payload(event: dict[str, Any]) -> tuple[dict[str, Any], str]:
        full_json = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        compact = {
            "p": "NEXUS",
            "v": 1,
            "j": event["job_id"],
            "pkg": event["package_id"],
            "e": event["event"],
            "r": event.get("robot_id"),
            "pr": event.get("previous_robot_id"),
            "t": int(event["tick"]),
            "pos": event.get("position"),
            "h": hashlib.sha256(full_json.encode("utf-8")).hexdigest()[:16],
        }
        compact = {key: value for key, value in compact.items() if value is not None}
        return dict(event), json.dumps(compact, sort_keys=True, separators=(",", ":"))

    def record_event(self, **event: Any) -> CustodyAttestation:
        payload, memo = self._canonical_payload(event)
        with self._lock:
            record = CustodyAttestation(
                sequence=len(self._records) + 1,
                payload=payload,
                memo=memo,
                chain_status=ChainStatus.PENDING if self.enabled else ChainStatus.NOT_REQUIRED,
                error=None if self.enabled else self.message,
            )
            self._records.append(record)
        if self.enabled and not self._closed:
            self._queue.put(record)
        return record

    def _work(self) -> None:
        while True:
            record = self._queue.get()
            try:
                if record is None:
                    return
                last_error: Exception | None = None
                for attempt in range(1, self.max_attempts + 1):
                    record.attempts = attempt
                    try:
                        signature = self.transport.submit(record.memo)  # type: ignore[union-attr]
                        with self._lock:
                            record.transaction_signature = signature
                            record.explorer_url = EXPLORER_TX.format(signature=signature)
                            record.chain_status = ChainStatus.CONFIRMED
                            record.error = None
                        break
                    except Exception as error:
                        last_error = error
                        if attempt < self.max_attempts:
                            time.sleep(0.1 * attempt)
                else:
                    with self._lock:
                        record.chain_status = ChainStatus.FAILED
                        record.error = str(last_error)
            finally:
                self._queue.task_done()

    def records(self, job_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records)
        if job_id is not None:
            records = [record for record in records if record.payload.get("job_id") == job_id]
        return [record.telemetry() for record in records]

    def get_status(self) -> dict[str, Any]:
        records = self.records()
        counts = {status.value.lower(): 0 for status in ChainStatus}
        for record in records:
            counts[record["chain_status"].lower()] += 1
        return {
            "enabled": self.enabled,
            "requested": self.requested,
            "cluster": self.cluster,
            "transport": self.transport_mode,
            "wallet": self.wallet_address,
            "balance": self.balance,
            "message": self.message,
            **counts,
            "attestations": records,
        }

    def wait_for_idle(self) -> None:
        self._queue.join()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=2.0)

    def __enter__(self) -> SolanaCustodyLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
