"""Mocked tests for the non-blocking Solana custody layer."""

import json
import threading
import unittest
from unittest.mock import Mock, patch

from nexus.demo import WarehouseDemo
from nexus.solana import ChainStatus, SolanaCliTransport, SolanaCustodyLedger


class FakeTransport:
    wallet_address = "NexusDevnetWallet111111111111111111111111111"
    balance = 1.92
    transport_mode = "NATIVE_CLI"

    def __init__(self, *, fail: bool = False, gate: threading.Event | None = None) -> None:
        self.fail = fail
        self.gate = gate
        self.memos: list[str] = []

    def submit(self, memo: str) -> str:
        self.memos.append(memo)
        if self.gate is not None:
            self.gate.wait(timeout=2)
        if self.fail:
            raise RuntimeError("mock RPC unavailable")
        return f"{'1' * 80}{len(self.memos):02d}"


def custody_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "protocol": "NEXUS",
        "version": 1,
        "job_id": "JOB-URGENT",
        "package_id": "MEDICAL-CRITICAL",
        "event": "JOB_CREATED",
        "robot_id": None,
        "previous_robot_id": None,
        "tick": 1200,
        "position": None,
    }
    event.update(overrides)
    return event


class SolanaLedgerTests(unittest.TestCase):
    def test_event_is_queued_without_blocking_caller(self) -> None:
        gate = threading.Event()
        ledger = SolanaCustodyLedger(enabled=True, transport=FakeTransport(gate=gate))
        record = ledger.record_event(**custody_event())
        self.assertEqual(record.chain_status, ChainStatus.PENDING)
        gate.set()
        ledger.wait_for_idle()
        ledger.close()

    def test_success_stores_signature_and_explorer_url(self) -> None:
        ledger = SolanaCustodyLedger(enabled=True, transport=FakeTransport())
        ledger.record_event(**custody_event())
        ledger.wait_for_idle()
        record = ledger.records()[0]
        self.assertEqual(record["chain_status"], "CONFIRMED")
        self.assertTrue(record["transaction_signature"])
        self.assertIn("cluster=devnet", record["explorer_url"])
        ledger.close()

    def test_repeated_failure_is_isolated(self) -> None:
        transport = FakeTransport(fail=True)
        ledger = SolanaCustodyLedger(enabled=True, transport=transport, max_attempts=2)
        ledger.record_event(**custody_event())
        ledger.wait_for_idle()
        record = ledger.records()[0]
        self.assertEqual(record["chain_status"], "FAILED")
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(len(transport.memos), 2)
        ledger.close()

    def test_disabled_mode_is_json_serializable(self) -> None:
        ledger = SolanaCustodyLedger(enabled=False)
        record = ledger.record_event(**custody_event())
        self.assertEqual(record.chain_status, ChainStatus.NOT_REQUIRED)
        json.dumps(ledger.get_status())
        ledger.close()

    def test_non_critical_jobs_do_not_create_attestations(self) -> None:
        ledger = SolanaCustodyLedger(enabled=True, transport=FakeTransport())
        demo = WarehouseDemo(custody_ledger=ledger)
        demo.start()
        for _ in range(10):
            demo.step()
        self.assertEqual(ledger.records(), [])
        demo.close()


class SolanaTransportSelectionTests(unittest.TestCase):
    @staticmethod
    def _successful_cli(args: list[str], **_kwargs: object) -> Mock:
        command = " ".join(args)
        if "transfer --help" in command:
            output = "Usage: solana transfer --with-memo <MEMO>"
        elif "config get" in command:
            output = "RPC URL: https://api.devnet.solana.com"
        elif "address" in command:
            output = "WalletPublicAddress1111111111111111111111111"
        elif "balance" in command:
            output = "1.92 SOL"
        else:
            output = "solana-cli 4.2.2"
        return Mock(returncode=0, stdout=output, stderr="")

    def test_native_cli_is_preferred_when_available(self) -> None:
        def which(name: str) -> str | None:
            return "C:\\solana.exe" if name == "solana" else "C:\\wsl.exe"

        with (
            patch("nexus.solana.shutil.which", side_effect=which),
            patch("nexus.solana.subprocess.run", side_effect=self._successful_cli) as run,
            patch.dict("os.environ", {"NEXUS_SOLANA_TRANSPORT": "auto"}),
        ):
            transport, message = SolanaCliTransport.discover()
        self.assertEqual(message, "ready")
        self.assertEqual(transport.transport_mode, "NATIVE_CLI")
        self.assertFalse(any(call.args[0][0] == "C:\\wsl.exe" for call in run.call_args_list))

    def test_wsl_is_selected_when_native_is_unavailable(self) -> None:
        def which(name: str) -> str | None:
            return "C:\\wsl.exe" if name == "wsl.exe" else None

        with (
            patch("nexus.solana.shutil.which", side_effect=which),
            patch("nexus.solana.subprocess.run", side_effect=self._successful_cli),
            patch.dict("os.environ", {"NEXUS_SOLANA_TRANSPORT": "auto"}),
        ):
            transport, message = SolanaCliTransport.discover()
        self.assertEqual(message, "ready")
        self.assertEqual(transport.transport_mode, "WSL_CLI")
        self.assertEqual(transport.command_prefix, ["C:\\wsl.exe", "--exec", "solana"])

    def test_both_unavailable_keeps_integration_disabled(self) -> None:
        with (
            patch("nexus.solana.shutil.which", return_value=None),
            patch.dict("os.environ", {"NEXUS_SOLANA_TRANSPORT": "auto"}),
        ):
            transport, message = SolanaCliTransport.discover()
        self.assertIsNone(transport)
        self.assertIn("No usable", message)

    def test_wsl_command_is_an_argument_array(self) -> None:
        transport = SolanaCliTransport.__new__(SolanaCliTransport)
        transport.command_prefix = ["wsl.exe", "--exec", "solana"]
        command = transport.build_command("balance")
        self.assertEqual(
            command,
            ["wsl.exe", "--exec", "solana", "balance", "--url", "devnet"],
        )

    def test_failed_wsl_probe_does_not_crash_nexus(self) -> None:
        def which(name: str) -> str | None:
            return "C:\\wsl.exe" if name == "wsl.exe" else None

        failure = Mock(returncode=1, stdout="", stderr="solana: command not found")
        with (
            patch("nexus.solana.shutil.which", side_effect=which),
            patch("nexus.solana.subprocess.run", return_value=failure),
            patch.dict(
                "os.environ",
                {"NEXUS_SOLANA_ENABLED": "1", "NEXUS_SOLANA_TRANSPORT": "wsl"},
            ),
        ):
            ledger = SolanaCustodyLedger()
        self.assertFalse(ledger.enabled)
        self.assertEqual(ledger.get_status()["transport"], "UNAVAILABLE")
        ledger.close()

    def test_critical_lifecycle_records_exact_seven_events_and_transfer(self) -> None:
        ledger = SolanaCustodyLedger(enabled=True, transport=FakeTransport())
        demo = WarehouseDemo(custody_ledger=ledger)
        demo.start()
        for _ in range(12_000):
            demo.step()
            if demo.finished:
                break
        self.assertTrue(demo.finished)
        ledger.wait_for_idle()
        records = ledger.records("JOB-URGENT")
        self.assertEqual(
            [record["event"] for record in records],
            [
                "JOB_CREATED",
                "CUSTODY_ASSIGNED",
                "PACKAGE_PICKED_UP",
                "ROBOT_UNAVAILABLE",
                "RECOVERY_POINT_CREATED",
                "CUSTODY_TRANSFER",
                "PACKAGE_DELIVERED",
            ],
        )
        transfer = next(record for record in records if record["event"] == "CUSTODY_TRANSFER")
        self.assertEqual((transfer["previous_robot_id"], transfer["robot_id"]), ("C", "A"))
        self.assertTrue(all(record["chain_status"] == "CONFIRMED" for record in records))
        json.dumps(demo.snapshot())
        demo.close()


if __name__ == "__main__":
    unittest.main()
