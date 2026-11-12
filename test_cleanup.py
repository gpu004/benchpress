"""Restart regressions use real child runners and local files as fake allocations."""

import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import lifecycle
import scenarios


class FakeSandbox:
    version = "local-fake"
    mutex = Lock()

    def __init__(self, provider, config, name, *, root, fault, **kwargs):
        self.root, self.fault, self.name, self.provider = root, fault, name, provider
        self.id = None

    def launch(self, timeout):
        with self.mutex:
            allocations = scenarios.read_rows(self.root / "cloud.jsonl")
            self.number = len(allocations) + 1
            self.remote_id = f"box-{self.number}"
            previous = sorted(path.name for path in self.root.glob("box-*"))
            (self.root / self.remote_id).write_text(self.name)
            with (self.root / "cloud.jsonl").open("a") as stream:
                scenarios.write_attempt(
                    stream,
                    {
                        "box": self.remote_id,
                        "name": self.name,
                        "provider": self.provider,
                        "previous_live": previous,
                    },
                )
        if self.number == 1 and self.fault == "creation_unconfirmed":
            raise TimeoutError("fake create succeeded but its response was lost")
        self.id = self.remote_id

    def payload(self, filename, *values):
        return [filename, *values]

    def exec(self, argv, timeout):
        if argv[0] == "machine.mjs":
            return "BENCHPRESS_MACHINE {}"
        if any("BENCHPRESS_PING" in str(value) for value in argv):
            return f"BENCHPRESS_PING {argv[-1]}"
        return "BENCHPRESS_RESULT " + json.dumps(
            {
                "workload": argv[-2],
                "token": argv[-1],
                "node": lifecycle.NODE,
            }
        )

    def placement(self):
        return {}

    def close(self):
        if self.number == 1 and self.fault == "failed":
            raise OSError("fake delete failed; allocation remains live")
        if self.number == 1 and self.fault == "creation_unconfirmed":
            return "creation_unconfirmed"
        (self.root / self.remote_id).unlink()
        return "removed"


def worker(root, fault):
    values = json.loads((root / "options.json").read_text())
    clock_day = values.pop("clock_day", 0)
    if values.get("start_date"):
        values["start_date"] = datetime.fromisoformat(values["start_date"]).date()
    options = SimpleNamespace(out=root / "experiment", **values)
    config = {
        "modal": {"app": "fake-app", "image": "fake-image"},
        "namespace": {"image": "fake-image", "size": "s"},
    }

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(days=clock_day)

    try:
        with (
            patch.object(lifecycle, "Sandbox", partial(FakeSandbox, root=root, fault=fault)),
            patch.object(scenarios, "datetime", Clock),
        ):
            scenarios.run_series(options, config)
    except (RuntimeError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 23
    return 0


class CleanupRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "experiment"
        self.options = dict(
            pilot=True,
            start_date=None,
            client_region="us-east",
            providers=["modal", "namespace"],
            seed=7256,
            mode="standard",
            workloads=["token"],
            variant="default",
            concurrency=2,
        )
        self.save_options()

    def save_options(self):
        (self.root / "options.json").write_text(json.dumps(self.options))

    def run_worker(self, fault="none"):
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(self.root), fault],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def allocations(self):
        return scenarios.read_rows(self.root / "cloud.jsonl")

    def rows(self):
        return scenarios.read_rows(self.out / "attempts.jsonl")

    def fail_first(self, fault="failed"):
        result = self.run_worker(fault)
        self.assertEqual(result.returncode, 23, result.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cleanup"], fault)
        self.assertEqual(len(self.allocations()), 1)
        self.assertTrue((self.root / "box-1").exists())
        return rows[0]

    def resolve_argv(self, row):
        return [
            sys.executable,
            str(Path(scenarios.ROOT) / "benchpress.py"),
            "resolve-cleanup",
            "--out",
            str(self.out),
            "--attempt",
            scenarios.cleanup_attempt_id(row),
            "--resolved-by",
            "test-operator",
            "--allocation-scope",
            f"local-fake account; creation name {row['name']}; verified box-1",
            "--evidence",
            "Matched exact creation name and removed local box-1; peer retained",
            "--confirmed-outcome",
            "removed",
        ]

    def assert_blocked(self, count=1):
        result = self.run_worker()
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn("Unresolved cleanup", result.stderr)
        self.assertEqual(len(self.allocations()), count)
        return result

    def test_saved_warnings_block_repeated_child_process_restarts(self):
        for fault in scenarios.CLEANUP_WARNINGS:
            with self.subTest(fault=fault):
                # Each fault has its own experiment and persistent fake cloud.
                with tempfile.TemporaryDirectory() as tmp:
                    self.root = Path(tmp)
                    self.out = self.root / "experiment"
                    self.save_options()
                    row = self.fail_first(fault)
                    before = (self.out / "attempts.jsonl").read_bytes()
                    for _ in range(2):
                        result = self.assert_blocked()
                        for value in (
                            scenarios.cleanup_attempt_id(row),
                            row["provider"],
                            row["name"],
                            "resolve-cleanup",
                            str(row.get("box_id")),
                        ):
                            self.assertIn(value if value != "None" else "null", result.stderr)
                    self.assertEqual((self.out / "attempts.jsonl").read_bytes(), before)

    def test_warning_from_previous_day_blocks_before_schedule(self):
        self.options.update(
            pilot=False,
            start_date=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
        )
        self.save_options()
        self.fail_first()
        self.options["clock_day"] = 1
        self.save_options()
        self.assert_blocked()

    def test_warning_blocks_before_changed_provider_and_workload_filters(self):
        row = self.fail_first()
        self.options.update(
            providers=["namespace" if row["provider"] == "modal" else "modal"], workloads=["one"]
        )
        self.save_options()
        self.assert_blocked()

    def test_exact_manual_resolution_allows_next_attempt_without_rewriting_history(self):
        for fault in scenarios.CLEANUP_WARNINGS:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                self.out = self.root / "experiment"
                self.save_options()
                row = self.fail_first(fault)
                before = (self.out / "attempts.jsonl").read_bytes()
                peer = self.root / "unrelated-peer"
                peer.write_text("another attempt")
                # Operator verification/remediation happens outside the runner.
                self.assertEqual((self.root / "box-1").read_text(), row["name"])
                (self.root / "box-1").unlink()
                result = subprocess.run(self.resolve_argv(row), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt["allocation"], scenarios.allocation(row))
                self.assertEqual(receipt["resolved_by"], "test-operator")
                self.assertTrue(datetime.fromisoformat(receipt["resolved_at"]).tzinfo)
                self.assertEqual((self.out / "attempts.jsonl").read_bytes(), before)
                self.assertEqual(peer.read_text(), "another attempt")
                self.assertEqual(len(self.allocations()), 1)
                result = self.run_worker()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.allocations()), 2)
                self.assertEqual(self.allocations()[1]["previous_live"], [])
                self.assertEqual(self.rows()[0], row)
                self.assertTrue((self.out / "attempts.jsonl").read_bytes().startswith(before))
                self.assertEqual(len(self.rows()), 2)
                self.assertEqual(self.run_worker().returncode, 0)
                self.assertEqual(len(self.allocations()), 2)

    def test_resolution_is_exact_and_uncertain_outcomes_stay_blocked(self):
        row = self.fail_first("creation_unconfirmed")
        kwargs = dict(
            resolved_by="operator",
            allocation_scope="fake exact creation",
            evidence="authoritative terminal evidence",
            confirmed_outcome="absent",
        )
        for change in (
            {"confirmed_outcome": "unknown"},
            {"evidence": " "},
            {"resolved_by": ""},
            {"allocation_scope": ""},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                scenarios.resolve_cleanup(
                    self.out, scenarios.cleanup_attempt_id(row), **(kwargs | change)
                )
        other = {
            "block": 99,
            "workload": "token",
            "provider": "modal",
            "scenario": "standard",
            "name": "another-creation",
            "box_id": "another-box",
            "cold": {"status": "ok"},
            "cleanup": "creation_unconfirmed",
        }
        with self.assertRaises(ValueError):
            scenarios.resolve_cleanup(self.out, scenarios.cleanup_attempt_id(other), **kwargs)
        self.assert_blocked()
        scenarios.resolve_cleanup(self.out, scenarios.cleanup_attempt_id(row), **kwargs)
        # A receipt for the first allocation cannot resolve a different attempt, even with its key.
        with (self.out / "attempts.jsonl").open("a") as stream:
            scenarios.write_attempt(stream, other)
        self.assert_blocked()
        receipt_path = (
            self.out / "cleanup-resolutions" / f"{scenarios.cleanup_attempt_id(row)}.json"
        )
        forged_path = receipt_path.with_name(f"{scenarios.cleanup_attempt_id(other)}.json")
        forged_path.write_bytes(receipt_path.read_bytes())
        self.assert_blocked()

    def test_resolution_persistence_failures_leave_restart_blocked(self):
        row = self.fail_first()
        kwargs = dict(
            resolved_by="operator",
            allocation_scope="fake exact allocation",
            evidence="terminal deletion receipt",
            confirmed_outcome="removed",
        )
        # Parent-directory fsync, receipt fsync, publication, and final directory fsync.
        for target, side_effect in (
            ("scenarios.os.fsync", OSError("parent fsync failed")),
            ("scenarios.os.fsync", [None, OSError("receipt fsync failed")]),
            ("scenarios.os.replace", OSError("rename failed")),
            ("scenarios.os.fsync", [None, None, OSError("directory fsync failed")]),
        ):
            with self.subTest(target=target, failure=str(side_effect)):
                with patch(target, side_effect=side_effect), self.assertRaises(OSError):
                    scenarios.resolve_cleanup(self.out, scenarios.cleanup_attempt_id(row), **kwargs)
                self.assertFalse(scenarios.cleanup_resolved(row, self.out))
                self.assertEqual(list((self.out / "cleanup-resolutions").iterdir()), [])
                self.assert_blocked()

    def test_run_and_resolution_share_experiment_lock(self):
        row = self.fail_first()
        with (self.out / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_worker()
            self.assertEqual(result.returncode, 23)
            resolution = subprocess.run(self.resolve_argv(row), capture_output=True, text=True)
            self.assertEqual(resolution.returncode, 1, resolution.stderr)
        self.assertFalse(scenarios.cleanup_resolved(row, self.out))
        self.assert_blocked()

    def test_healthy_saved_attempts_still_skip(self):
        self.assertEqual(self.run_worker().returncode, 0)
        before = (self.out / "attempts.jsonl").read_bytes()
        self.assertEqual(len(self.allocations()), 2)
        self.assertEqual(self.run_worker().returncode, 0)
        self.assertEqual((self.out / "attempts.jsonl").read_bytes(), before)
        self.assertEqual(len(self.allocations()), 2)
        self.assertFalse(list(self.root.glob("box-*")))

    def test_setup_error_stops_after_save_but_is_not_a_cleanup_warning(self):
        args = SimpleNamespace(out=self.out, **self.options)
        config = {
            "modal": {"app": "fake-app", "image": "fake-image"},
            "namespace": {"image": "fake-image", "size": "s"},
        }
        with (
            patch.object(lifecycle, "Sandbox", side_effect=RuntimeError("setup unavailable")),
            self.assertRaisesRegex(RuntimeError, "setup or cleanup needs attention"),
        ):
            scenarios.run_series(args, config)
        row = self.rows()[0]
        self.assertEqual(row["cold"]["status"], "setup_error")
        self.assertFalse(scenarios.cleanup_warning(row))
        self.assertEqual(self.run_worker().returncode, 0)
        self.assertEqual(len(self.allocations()), 1)

    def test_unsupported_and_intentionally_retained_boxes_do_not_block(self):
        self.options.update(mode="resume", providers=["namespace"])
        self.save_options()
        self.assertEqual(self.run_worker().returncode, 0)
        row = self.rows()[0]
        self.assertEqual(row["cold"]["status"], "unsupported")
        self.assertEqual(row["cleanup"], "not_run")
        self.assertEqual(self.run_worker().returncode, 0)
        self.assertEqual(self.allocations(), [])
        scenarios.require_cleanup_resolved([row | {"cleanup": "existing_box_kept"}], self.out)

    def test_burst_saves_inflight_results_and_stops_before_next_provider(self):
        self.options.update(mode="burst")
        self.save_options()
        result = self.run_worker("failed")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.allocations()), 2)
        self.assertEqual({row["provider"] for row in self.rows()}, {"modal"})
        self.assert_blocked(count=2)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        sys.exit(worker(Path(sys.argv[2]), sys.argv[3]))
    unittest.main()
