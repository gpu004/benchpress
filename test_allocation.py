"""Crash regression tests derived from #1's local fake-provider reproducer.

Only child runner processes receive SIGKILL. Remote state is ordinary local files
outside the experiment directory; no credentials or cloud requests are used.
"""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import modal

import allocation
import lifecycle
import scenarios
from allocation import Allocation, AllocationJournal, RecoveryBlocked, durable_json

ROOT = Path(__file__).resolve().parent


def event(root, **record):
    fd = os.open(root / "cloud.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(record) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def records(root):
    path = root / "cloud.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def remote(root):
    return [json.loads(path.read_text()) for path in (root / "remote").glob("*.json")]


def kill():
    os.kill(os.getpid(), signal.SIGKILL)


def worker(root, fault, lookup, mode, provider):
    def stop(point):
        if fault == point:
            kill()

    class Handle:
        def __init__(self, path):
            self.path = path
            self.object_id = json.loads(path.read_text())["id"]

        def get_tags(self):
            if lookup == "wrong-owner":
                return {"benchpress_allocation": "another-worker"}
            return json.loads(self.path.read_text())["tags"]

        def terminate(self, wait=True):
            box = json.loads(self.path.read_text())
            stop("recovery_before_delete")
            box["live"] = False
            durable_json(self.path, box)
            event(root, op="delete", id=box["id"])
            stop("recovery_after_delete")

    def from_name(app, name, **kwargs):
        event(root, op="lookup", name=name)
        if lookup == "error":
            raise OSError("fake provider unavailable")
        path = root / "remote" / (name + ".json")
        if lookup == "empty" or not path.exists():
            raise modal.exception.NotFoundError("fake lookup not yet visible")
        return Handle(path)

    def from_id(box_id):
        if lookup == "error":
            raise OSError("fake provider unavailable")
        matches = [
            path
            for path in (root / "remote").glob("*.json")
            if json.loads(path.read_text())["id"] == box_id
        ]
        if len(matches) != 1:
            raise modal.exception.NotFoundError("fake ID not found")
        return Handle(matches[0])

    class FakeBox:
        version = "local-fake"

        def __init__(self, provider, config, name, **kwargs):
            self.id = None
            self.name = name
            self.path = root / "remote" / (name + ".json")
            stop("setup")
            if fault == "setup_error":
                raise RuntimeError("fake adapter setup failed")

        def launch(self, timeout):
            stop("before_dispatch")
            previous = [
                item["id"] for item in remote(root) if item["live"] and item["id"] != "peer"
            ]
            box_id = "box-" + self.name
            durable_json(
                self.path,
                {"id": box_id, "live": True, "tags": {"benchpress_allocation": self.name}},
            )
            event(root, op="create", id=box_id, name=self.name, previous_live=previous)
            stop("create_response_gap")
            if fault == "unconfirmed":
                raise TimeoutError("fake lost response")
            self.id = box_id

        def payload(self, script, *values):
            return [script, *values]

        def exec(self, argv, timeout):
            if argv[0] == "machine.mjs":
                return "BENCHPRESS_MACHINE {}"
            if lifecycle.PING in argv:
                stop("readiness")
                return f"BENCHPRESS_PING {argv[-1]}"
            stop("workload")
            workload, token = argv[-2:]
            result = {"workload": workload, "token": token, "node": lifecycle.NODE}
            if workload == "one":
                result.update(
                    bytes=32 * 1024**2,
                    records=524288,
                    checksum=lifecycle.CHECKSUMS["one"],
                    read_ms=1,
                    compute_ms=1,
                )
            return "BENCHPRESS_RESULT " + json.dumps(result)

        def placement(self):
            return {}

        def close(self):
            stop("cleanup_before_delete")
            if fault == "cleanup_failed":
                raise OSError("fake delete failed")
            if fault == "unconfirmed":
                return "creation_unconfirmed"
            if self.path.exists():
                box = json.loads(self.path.read_text())
                box["live"] = False
                durable_json(self.path, box)
                event(root, op="delete", id=box["id"])
            stop("cleanup_after_delete")
            return "removed"

    save = Allocation.save

    def save_state(entry, **changes):
        stop("before_result_committed") if changes.get("phase") == "result_committed" else None
        save(entry, **changes)
        if changes.get("phase") == "allocated":
            stop("allocated")
        if changes.get("phase") == "cleanup_confirmed":
            stop("cleanup_confirmed")

    write_attempt = scenarios.write_attempt

    def write_row(stream, row):
        text = json.dumps(row) + "\n"
        if fault == "result_partial":
            stream.write(text[: len(text) // 2])
            stream.flush()
            os.fsync(stream.fileno())
            kill()
        if fault in ("result_write", "result_flush", "result_before_fsync"):
            stream.write(text)
            stop("result_write")
            stream.flush()
            stop("result_flush")
            stop("result_before_fsync")
        write_attempt(stream, row)
        stop("result_fsync")

    options = SimpleNamespace(
        out=root / "experiment",
        pilot=True,
        start_date=None,
        client_region="us-east",
        providers=[provider],
        seed=7256,
        mode=mode,
        workloads=["token", "one"] if mode == "standard" else None,
        variant="default",
        concurrency=4,
    )
    config = {
        provider: {
            "app": "fake-app",
            "image": "fake-image",
            "snapshot": "fake-snapshot",
            "size": "s",
        }
    }
    try:
        with (
            patch.object(lifecycle, "Sandbox", FakeBox),
            patch.object(modal.Sandbox, "from_name", side_effect=from_name),
            patch.object(modal.Sandbox, "from_id", side_effect=from_id),
            patch.object(Allocation, "save", save_state),
            patch.object(scenarios, "write_attempt", write_row),
            patch.object(scenarios, "fingerprint", return_value="fake-provider-test"),
        ):
            scenarios.run_series(options, config)
    except (ValueError, RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 23
    return 0


class CrashTests(unittest.TestCase):
    def invoke(self, root, fault="none", lookup="normal", mode="standard", provider="modal"):
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                str(root),
                "--fault",
                fault,
                "--lookup",
                lookup,
                "--mode",
                mode,
                "--provider",
                provider,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def root(self, tmp):
        root = Path(tmp)
        (root / "remote").mkdir()
        # Recovery must never delete a peer allocation, even when its own lookup fails.
        durable_json(
            root / "remote/peer.json",
            {"id": "peer", "live": True, "tags": {"benchpress_allocation": "peer"}},
        )
        return root

    def entries(self, root):
        return [
            json.loads(path.read_text())
            for path in (root / "experiment/allocations").glob("*.json")
        ]

    def assert_safety(self, root):
        self.assertTrue(
            all(not row["previous_live"] for row in records(root) if row["op"] == "create")
        )
        self.assertTrue(json.loads((root / "remote/peer.json").read_text())["live"])
        self.assertFalse(any(row.get("id") == "peer" for row in records(root)))

    def test_kill_at_lifecycle_and_result_boundaries(self):
        faults = (
            "create_response_gap",
            "allocated",
            "readiness",
            "workload",
            "cleanup_before_delete",
            "cleanup_after_delete",
            "cleanup_confirmed",
            "result_write",
            "result_flush",
            "result_before_fsync",
            "result_fsync",
            "before_result_committed",
        )
        for fault in faults:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                first = self.invoke(root, fault)
                self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
                states = self.entries(root)
                self.assertEqual(len(states), 1)
                self.assertEqual(states[0]["generation"], 1)
                second = self.invoke(root)
                self.assertEqual(second.returncode, 0, second.stderr)
                rows = scenarios.read_rows(root / "experiment/attempts.jsonl")
                self.assertEqual(len(rows), 2)
                self.assertEqual(len({row["allocation_id"] for row in rows}), 2)
                self.assertTrue(
                    all(state["phase"] == "result_committed" for state in self.entries(root))
                )
                self.assertEqual(self.invoke(root).returncode, 0)
                self.assertEqual(len(scenarios.read_rows(root / "experiment/attempts.jsonl")), 2)
                self.assertEqual(len([row for row in records(root) if row["op"] == "create"]), 2)
                self.assert_safety(root)

    def test_intent_before_dispatch_and_delayed_or_ambiguous_lookup_block(self):
        cases = [
            ("before_dispatch", "normal"),
            ("setup", "normal"),
            ("create_response_gap", "empty"),
            ("create_response_gap", "error"),
            ("create_response_gap", "wrong-owner"),
        ]
        for fault, lookup in cases:
            with self.subTest(fault=fault, lookup=lookup), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                self.assertEqual(self.invoke(root, fault).returncode, -signal.SIGKILL)
                before = len([row for row in records(root) if row["op"] == "create"])
                for _ in range(2):
                    restarted = self.invoke(root, lookup=lookup)
                    self.assertEqual(restarted.returncode, 23, restarted.stderr)
                    self.assertIn("reconcile", restarted.stderr)
                self.assertEqual(
                    len([row for row in records(root) if row["op"] == "create"]), before
                )
                self.assert_safety(root)

    def test_recovery_interruption_converges_and_preserves_peers(self):
        for point in ("recovery_before_delete", "recovery_after_delete"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                self.assertEqual(
                    self.invoke(root, "create_response_gap").returncode, -signal.SIGKILL
                )
                for _ in range(2):
                    self.assertEqual(self.invoke(root, point).returncode, -signal.SIGKILL)
                    self.assertEqual(
                        len([row for row in records(root) if row["op"] == "create"]), 1
                    )
                self.assertEqual(self.invoke(root).returncode, 0)
                self.assert_safety(root)

    def test_partial_result_blocks_without_discarding_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(self.invoke(root, "result_partial").returncode, -signal.SIGKILL)
            path = root / "experiment/attempts.jsonl"
            before = path.read_bytes()
            restarted = self.invoke(root)
            self.assertEqual(restarted.returncode, 23)
            self.assertIn("Invalid results", restarted.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertIsNotNone(self.entries(root)[0]["result"])
            self.assertEqual(len([row for row in records(root) if row["op"] == "create"]), 1)
            self.assert_safety(root)

    def test_malformed_results_do_not_bypass_remote_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(self.invoke(root, "allocated").returncode, -signal.SIGKILL)
            (root / "experiment/attempts.jsonl").write_text('{"torn":')
            restarted = self.invoke(root)
            self.assertEqual(restarted.returncode, 23)
            self.assertEqual(self.entries(root)[0]["phase"], "cleanup_confirmed")
            self.assertFalse(any(box["live"] for box in remote(root) if box["id"] != "peer"))
            self.assert_safety(root)

    def test_provider_without_lookup_requires_manual_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(
                self.invoke(root, "create_response_gap", provider="tesser").returncode,
                -signal.SIGKILL,
            )
            restarted = self.invoke(root, provider="tesser")
            self.assertEqual(restarted.returncode, 23)
            self.assertIn("no verified automatic recovery contract", restarted.stderr)
            state = self.entries(root)[0]
            self.assertIsNone(state["provider_identity"])
            for path in (root / "remote").glob("benchpress-*.json"):
                box = json.loads(path.read_text())
                box["live"] = False
                durable_json(path, box)
            result = subprocess.run(
                [
                    sys.executable,
                    "benchpress.py",
                    "reconcile",
                    "--out",
                    str(root / "experiment"),
                    "--allocation",
                    state["allocation_id"],
                    "--confirm-terminal",
                    "--actor",
                    "test-operator",
                    "--evidence",
                    "Fake provider confirms removal and no in-flight create",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.invoke(root, provider="tesser").returncode, 0)
            rows = scenarios.read_rows(root / "experiment/attempts.jsonl")
            self.assertEqual(rows[0]["cold"]["status"], "interrupted")
            self.assertEqual(
                self.entries(root)[0].get("manual_resolution", {}).get("actor")
                or self.entries(root)[1].get("manual_resolution", {}).get("actor"),
                "test-operator",
            )
            self.assert_safety(root)

    def test_interrupted_measurement_is_reported_once_as_censored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(self.invoke(root, "workload").returncode, -signal.SIGKILL)
            self.assertEqual(self.invoke(root).returncode, 0)
            rows = scenarios.read_rows(root / "experiment/attempts.jsonl")
            interrupted = [row for row in rows if row["cold"]["status"] == "interrupted"]
            self.assertEqual(len(interrupted), 1)
            result = subprocess.run(
                [sys.executable, "benchpress.py", "report", str(root / "experiment")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            model = json.loads((root / "experiment/report.json").read_text())
            summary = next(
                row
                for row in model["rows"]
                if row["workload"] == interrupted[0]["workload"] and row["phase"] == "cold"
            )
            self.assertEqual(summary["attempts"], 1)
            self.assertEqual(summary["verified"], 0)
            self.assertEqual(summary["wall_p50_ms"], 120000)

    def test_healthy_burst_keeps_worker_results_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            result = self.invoke(root, mode="burst")
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = scenarios.read_rows(root / "experiment/attempts.jsonl")
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({row["allocation_id"] for row in rows}), 4)
            self.assertTrue(
                all(state["phase"] == "result_committed" for state in self.entries(root))
            )
            self.assertFalse(any(box["live"] for box in remote(root) if box["id"] != "peer"))

    def test_malformed_state_blocks_before_any_provider_call(self):
        for corruption in ("json", "phase", "identity", "experiment", "missing_id"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                self.invoke(root, "allocated")
                path = next((root / "experiment/allocations").glob("*.json"))
                state = json.loads(path.read_text())
                if corruption == "json":
                    path.write_text("[1, 2]")
                else:
                    field, value = {
                        "phase": ("phase", "unknown"),
                        "identity": ("provider_identity", {}),
                        "experiment": ("experiment_id", "wrong"),
                        "missing_id": ("box_id", None),
                    }[corruption]
                    state[field] = value
                    durable_json(path, state)
                before = records(root)
                restarted = self.invoke(root)
                self.assertEqual(restarted.returncode, 23, restarted.stderr)
                self.assertIn("Malformed allocation", restarted.stderr)
                self.assertEqual(records(root), before)

    def test_saved_failure_recovers_without_rewriting_measurement(self):
        for fault in ("cleanup_failed", "unconfirmed"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                self.assertEqual(self.invoke(root, fault).returncode, 23)
                original = scenarios.read_rows(root / "experiment/attempts.jsonl")[0]
                restarted = self.invoke(root)
                self.assertEqual(restarted.returncode, 0, restarted.stderr)
                self.assertEqual(
                    scenarios.read_rows(root / "experiment/attempts.jsonl")[0], original
                )
                self.assert_safety(root)

    def test_lock_and_healthy_completed_attempt_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(self.invoke(root).returncode, 0)
            before = records(root)
            self.assertEqual(self.invoke(root).returncode, 0)
            self.assertEqual(records(root), before)
            with (root / "experiment/.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(self.invoke(root).returncode, 23)
            self.assertEqual(records(root), before)

    def test_burst_journals_each_worker_and_recovers_before_rejecting_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            self.assertEqual(
                self.invoke(root, "allocated", mode="burst").returncode, -signal.SIGKILL
            )
            before = len([row for row in records(root) if row["op"] == "create"])
            restarted = self.invoke(root, mode="burst")
            self.assertEqual(restarted.returncode, 23)
            self.assertFalse(any(box["live"] for box in remote(root) if box["id"] != "peer"))
            self.assertEqual(len([row for row in records(root) if row["op"] == "create"]), before)
            # A worker killed before dispatch may have an empty lookup. It stays
            # pending while positively identified allocations are cleaned first.
            self.assertTrue(
                all(
                    state["phase"] in ("create_pending", "cleanup_confirmed", "result_committed")
                    for state in self.entries(root)
                )
            )
            self.assertEqual(self.invoke(root, mode="burst").returncode, 23)
            self.assertEqual(len([row for row in records(root) if row["op"] == "create"]), before)


class JournalTests(unittest.TestCase):
    def journal(self, tmp):
        return AllocationJournal(Path(tmp), {"experiment_id": "0" * 32})

    def test_intent_sync_order_and_failures_prevent_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            events = []
            real_fsync, real_replace = os.fsync, os.replace

            def sync(fd):
                events.append("fsync")
                real_fsync(fd)

            def replace(*args):
                events.append("replace")
                real_replace(*args)

            with (
                patch.object(allocation.os, "fsync", sync),
                patch.object(allocation.os, "replace", replace),
            ):
                entry = journal.begin(
                    "modal", {"app": "fake", "secret": "never-store"}, "standard", "token", 0
                )
            self.assertEqual(events, ["fsync", "replace", "fsync"])
            self.assertNotIn("never-store", entry.path.read_text())
            for field in (
                "experiment_id",
                "attempt_key",
                "allocation_id",
                "generation",
                "provider_identity",
                "phase",
                "created_at",
                "updated_at",
                "box_id",
            ):
                self.assertIn(field, entry.state)
        for failure in ("fsync", "replace", "sync_directory"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                journal = self.journal(tmp)
                target = allocation if failure == "sync_directory" else allocation.os
                with (
                    patch.object(target, failure, side_effect=OSError("fake disk failure")),
                    patch.object(lifecycle, "Sandbox") as create,
                ):
                    with self.assertRaises(OSError):
                        lifecycle.run_mode(
                            "modal", {}, "standard", workload="token", block=0, journal=journal
                        )
                    create.assert_not_called()

    def test_manual_receipt_failure_leaves_allocation_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = self.journal(tmp)
            entry = journal.begin("tesser", {}, "standard", "token", 0)
            durable_json(root / "experiment.json", {"experiment_id": journal.experiment_id})
            original = entry.path.read_bytes()
            with patch.object(allocation, "durable_json", side_effect=OSError("fake disk full")):
                with self.assertRaises(OSError):
                    allocation.reconcile_manual(
                        root, entry.state["allocation_id"], actor="test", evidence="terminal"
                    )
            self.assertEqual(entry.path.read_bytes(), original)
            with self.assertRaises(RecoveryBlocked):
                self.journal(tmp).recover()

    def test_e2b_known_id_verifies_metadata_and_empty_lookup_stays_blocked(self):
        from unittest.mock import Mock

        import e2b

        import providers

        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            entry = journal.begin("e2b", {}, "standard", "token", 0)
            with (
                patch.object(e2b.Sandbox, "get_info") as info,
                patch.object(e2b.Sandbox, "kill") as remove,
            ):
                with self.assertRaisesRegex(RuntimeError, "manual reconciliation"):
                    providers.recover_allocation(entry)
                info.assert_not_called()
                remove.assert_not_called()
            entry.allocated("known-id")
            with (
                patch.object(providers, "e2b_key", return_value="fake-key"),
                patch.object(
                    e2b.Sandbox,
                    "get_info",
                    return_value=Mock(metadata={"benchpress_trial": "peer"}, sandbox_id="known-id"),
                ),
                patch.object(e2b.Sandbox, "kill") as remove,
            ):
                with self.assertRaisesRegex(RuntimeError, "ownership"):
                    providers.recover_allocation(entry)
                remove.assert_not_called()
            with (
                patch.object(providers, "e2b_key", return_value="fake-key"),
                patch.object(
                    e2b.Sandbox,
                    "get_info",
                    return_value=Mock(
                        metadata=entry.state["provider_identity"]["metadata"], sandbox_id="known-id"
                    ),
                ),
                patch.object(e2b.Sandbox, "kill") as remove,
            ):
                providers.recover_allocation(entry)
                remove.assert_called_once_with("known-id", api_key="fake-key", request_timeout=15)
            with (
                patch.object(providers, "e2b_key", return_value="fake-key"),
                patch.object(
                    e2b.Sandbox,
                    "get_info",
                    side_effect=e2b.SandboxNotFoundException("unknown account or absent"),
                ),
                patch.object(e2b.Sandbox, "kill") as remove,
            ):
                with self.assertRaises(e2b.SandboxNotFoundException):
                    providers.recover_allocation(entry)
                remove.assert_not_called()

    def test_modal_resume_lost_response_blocks_without_name_lookup(self):
        import providers

        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            entry = journal.begin("modal", {"app": "fake"}, "resume", "token", 0)
            with patch.object(modal.Sandbox, "from_name") as lookup:
                with self.assertRaisesRegex(RuntimeError, "persistent create has no name"):
                    providers.recover_allocation(entry)
                lookup.assert_not_called()

    def test_lost_response_cleanup_persists_discovered_id_before_delete(self):
        from unittest.mock import Mock

        import e2b

        import providers

        for provider in ("modal", "e2b"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                entry = self.journal(tmp).begin(provider, {"app": "fake"}, "standard", "token", 0)
                box = providers.Sandbox.__new__(providers.Sandbox)
                box.provider, box.settings, box.name = (
                    provider,
                    {"app": "fake"},
                    entry.state["name"],
                )
                box.id, box.handle, box.existing, box.resume = None, None, None, False
                box.allocation, box.e2b_api_key = entry, "fake-key"

                def interrupted_delete(*args, **kwargs):
                    saved = json.loads(entry.path.read_text())
                    self.assertEqual(saved["phase"], "allocated")
                    self.assertEqual(saved["box_id"], "discovered-id")
                    raise OSError("interrupted removal after ID persistence")

                handle = Mock(object_id="discovered-id")
                handle.get_tags.return_value = entry.state["provider_identity"].get("tags")
                handle.terminate.side_effect = interrupted_delete
                matches = Mock()
                matches.next_items.return_value = [SimpleNamespace(sandbox_id="discovered-id")]
                with (
                    patch.object(box, "modal", modal, create=True),
                    patch.object(box, "e2b", e2b, create=True),
                    patch.object(modal.Sandbox, "from_name", return_value=handle),
                    patch.object(e2b.Sandbox, "list", return_value=matches),
                    patch.object(e2b.Sandbox, "kill", side_effect=interrupted_delete),
                ):
                    with self.assertRaisesRegex(OSError, "interrupted removal"):
                        box.close()
                self.assertEqual(self.journal(tmp).entries[0].state["box_id"], "discovered-id")

    def test_legacy_cleanup_warning_and_missing_receipts_block(self):
        for cleanup in ("failed", "creation_unconfirmed"):
            with self.subTest(cleanup=cleanup), tempfile.TemporaryDirectory() as tmp:
                journal = self.journal(tmp)
                rows = [
                    {
                        "block": 0,
                        "provider": "modal",
                        "workload": "token",
                        "cold": {"status": "failed"},
                        "cleanup": cleanup,
                    }
                ]
                with (Path(tmp) / "attempts.jsonl").open("a") as stream:
                    with self.assertRaisesRegex(RecoveryBlocked, "Legacy cleanup warning"):
                        journal.reconcile_results(rows, stream, scenarios.write_attempt)
                    rows[0]["allocation_id"] = "missing"
                    with self.assertRaisesRegex(RecoveryBlocked, "Missing journal"):
                        journal.reconcile_results(rows, stream, scenarios.write_attempt)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--worker", type=Path)
        parser.add_argument("--fault", default="none")
        parser.add_argument("--lookup", default="normal")
        parser.add_argument("--mode", default="standard")
        parser.add_argument("--provider", default="modal")
        options = parser.parse_args()
        sys.exit(
            worker(options.worker, options.fault, options.lookup, options.mode, options.provider)
        )
    unittest.main()
