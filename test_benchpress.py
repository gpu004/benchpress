import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import benchpress


def stage_payload(tmp):
    root = Path(tmp)
    for name in ("fixture.mjs", "run.mjs", "cores-worker.mjs"):
        shutil.copyfile(benchpress.PAYLOAD / name, root / name)
    subprocess.run(["node", "fixture.mjs"], cwd=root, check=True)
    shutil.copytree(benchpress.PAYLOAD / "install-package", root / "package")
    subprocess.run(["tar", "-czf", "install-package.tgz", "package"], cwd=root, check=True)
    node = Path(shutil.which("node")).resolve()
    (root / "node").symlink_to(node)
    npm_candidates = [
        node.parent.parent / "lib/node_modules/npm",
        Path("/opt/homebrew/lib/node_modules/npm"),
        Path("/usr/local/lib/node_modules/npm"),
        Path("/usr/lib/node_modules/npm"),
    ]
    npm_root = next((path for path in npm_candidates if (path / "bin/npm-cli.js").exists()), None)
    if npm_root is None:
        return False
    (root / "node_modules").mkdir()
    shutil.copytree(npm_root, root / "node_modules" / "npm")
    return True


class WorkloadTests(unittest.TestCase):
    def test_prepare_copies_the_npm_package_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            npm_cli = root / "lib/node_modules/npm/bin/npm-cli.js"
            npm_cli.parent.mkdir(parents=True)
            npm_cli.write_text("// npm launcher\n")
            node = bin_dir / "node"
            node.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                f'  -p) printf "%s\\n" "{benchpress.NODE}" ;;\n'
                '  -e) printf "%s\\n" "$TEST_NPM_CLI" ;;\n'
                "  */fixture.mjs) ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n"
            )
            node.chmod(0o755)
            dest = root / "payload"
            subprocess.run(
                ["sh", str(benchpress.PAYLOAD / "prepare.sh"), str(dest)],
                env={
                    **os.environ,
                    "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "BENCHPRESS_VARIANT": "default",
                    "TEST_NPM_CLI": str(npm_cli),
                },
                check=True,
            )
            self.assertEqual(
                (dest / "node_modules/npm/bin/npm-cli.js").read_text(), npm_cli.read_text()
            )

    @unittest.skipUnless(shutil.which("node"), "requires Node.js")
    def test_payload_validates_and_rejects_a_bad_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            has_npm = stage_payload(tmp)
            node = subprocess.check_output(
                ["node", "-p", "process.versions.node"], text=True
            ).strip()
            with patch("lifecycle.NODE", node):
                for workload in benchpress.WORKLOADS:
                    if workload == "install" and not has_npm:
                        continue
                    output = subprocess.check_output(
                        ["node", "run.mjs", workload, "nonce"], cwd=tmp, text=True
                    )
                    result = benchpress.verify(output, workload, "nonce")
                    with self.assertRaisesRegex(ValueError, "token"):
                        benchpress.verify(output, workload, "wrong-nonce")
                    if workload != "token":
                        result["checksum"] = "0" * 64
                        with self.assertRaisesRegex(ValueError, "checksum"):
                            benchpress.verify(
                                "BENCHPRESS_RESULT " + json.dumps(result), workload, "nonce"
                            )

    def test_timeout_removes_fresh_box_and_skips_warm(self):
        class Box:
            version = "test"
            id = "fake-box"

            def launch(self, timeout):
                time.sleep(1)

            def close(self):
                return "removed"

        with patch("lifecycle.Sandbox", return_value=Box()), patch("lifecycle.DEADLINE", 0.03):
            row = benchpress.run_mode("modal", {}, "standard", workload="token", block=0)
        self.assertEqual(row["cold"]["status"], "timeout")
        self.assertEqual(row["cleanup"], "removed")
        self.assertEqual(row["warm"]["status"], "not_run")

    def test_schedule_and_bootstrap(self):
        tasks = list(benchpress.schedule(benchpress.PROVIDERS, seed=7256))
        self.assertEqual(
            len(tasks),
            len(benchpress.WORKLOADS) * len(benchpress.PROVIDERS) * benchpress.SERIES_BLOCKS,
        )
        self.assertEqual(len(tasks), len(set(tasks)))
        self.assertNotEqual(tasks[:12], [(1, work, provider) for _, work, provider in tasks[12:24]])
        pairs = {index: (3 * (index + 1), index + 1) for index in range(30)}
        self.assertEqual(benchpress.interval(pairs), (3.0, 3.0))
        self.assertEqual(benchpress.bootstrap_median([10.0, 20.0, 30.0]), (20.0, 10.0, 30.0))

    def test_ratio_threshold_requires_29_verified_and_pairs(self):
        self.assertEqual(benchpress.RATIO_MIN_VERIFIED, 29)
        self.assertEqual(benchpress.SERIES_BLOCKS, 30)
        self.assertTrue(benchpress.ratio_ready(29, 29, 29))
        self.assertTrue(benchpress.ratio_ready(30, 30, 30))
        self.assertFalse(benchpress.ratio_ready(28, 29, 29))
        self.assertFalse(benchpress.ratio_ready(29, 28, 29))
        self.assertFalse(benchpress.ratio_ready(29, 29, 28))

    def test_report_writes_csv_json_plots_and_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "schema": 2,
                "pilot": True,
                "start_date": "None",
                "client_region": "us-east",
                "providers": ["tesser", "modal"],
                "workloads": ["one", "ten", "token"],
                "scenario": "standard",
                "variant": "default",
                "deadline_seconds": 120,
                "client": "test",
                "node": "26.13.1",
                "source_sha256": "abc123fingerprint",
                "seed": 7256,
                "config": {
                    "tesser": {"usd_per_hour": 0.08, "price_date": "2026-11-11"},
                    "modal": {"usd_per_hour": 0.24, "price_date": "2026-11-11"},
                },
            }
            (root / "experiment.json").write_text(json.dumps(manifest) + "\n")
            attempts = []
            for provider, wall, compute in (
                ("tesser", 5000.0, 150.0),
                ("tesser", 5200.0, 160.0),
                ("modal", 1200.0, 220.0),
                ("modal", 1300.0, 230.0),
            ):
                attempts.append(
                    {
                        "provider": provider,
                        "workload": "one",
                        "block": 0,
                        "scenario": "standard",
                        "started_at": "2026-11-11T12:00:00+00:00",
                        "cold": {
                            "status": "ok",
                            "launch_ms": 100.0,
                            "wall_ms": wall,
                            "guest": {"read_ms": 10.0, "compute_ms": compute},
                        },
                        "warm": {
                            "status": "ok",
                            "wall_ms": wall / 4,
                            "guest": {"read_ms": 8.0, "compute_ms": compute * 0.9},
                        },
                        "estimated_usd": 0.001,
                        "cleanup": "removed",
                    }
                )
            for provider in ("tesser", "modal"):
                attempts.append(
                    {
                        "provider": provider,
                        "workload": "token",
                        "block": 0,
                        "scenario": "standard",
                        "started_at": "2026-11-12T12:00:00+00:00",
                        "cold": {
                            "status": "ok",
                            "launch_ms": 80.0,
                            "wall_ms": 900.0,
                            "guest": {"workload": "token"},
                        },
                        "warm": {"status": "ok", "wall_ms": 300.0, "guest": {"workload": "token"}},
                        "estimated_usd": 0.002,
                        "cleanup": "removed",
                    }
                )
                attempts.append(
                    {
                        "provider": provider,
                        "workload": "ten",
                        "block": 0,
                        "scenario": "standard",
                        "started_at": "2026-11-11T18:00:00+00:00",
                        "cold": {
                            "status": "ok",
                            "launch_ms": 90.0,
                            "wall_ms": 7000.0,
                            "guest": {"read_ms": 12.0, "compute_ms": 1600.0},
                        },
                        "warm": {
                            "status": "ok",
                            "wall_ms": 2500.0,
                            "guest": {"read_ms": 11.0, "compute_ms": 1500.0},
                        },
                        "estimated_usd": 0.003,
                        "cleanup": "removed",
                    }
                )
            (root / "attempts.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in attempts)
            )
            with patch("builtins.print"):
                benchpress.report(root)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["source_sha256"], "abc123fingerprint")
            self.assertEqual(report["date_range"], {"start": "2026-11-11", "end": "2026-11-12"})
            self.assertEqual(report["price_dates"], ["2026-11-11"])
            self.assertIn("us-east", report["client_region_limitation"])
            markdown = (root / "report.md").read_text()
            self.assertIn("Source fingerprint: `abc123fingerprint`", markdown)
            self.assertIn("Date range: 2026-11-11 to 2026-11-12", markdown)
            self.assertIn("Price date: 2026-11-11", markdown)
            self.assertIn("Client region: us-east", markdown)
            self.assertIn("Single-client-region limitation", markdown)
            self.assertIn("Guest compute p50 ms (95%)", markdown)
            self.assertRegex(markdown, r"5100\.00 \(5000\.00–5200\.00\)")
            csv_text = (root / "report.csv").read_text()
            self.assertIn("wall_p50_ms,wall_p50_low_ms,wall_p50_high_ms", csv_text)
            self.assertIn("guest_compute_p50_ms", csv_text)
            self.assertTrue((root / "plots" / "one-cold-wall.svg").exists())
            self.assertTrue((root / "plots" / "one-cold-guest-compute.svg").exists())
            self.assertTrue((root / "plots" / "one-warm-first-wall.svg").exists())
            self.assertTrue((root / "plots" / "one-warm-median-wall.svg").exists())
            self.assertFalse((root / "plots" / "token-cold-guest-compute.svg").exists())
            self.assertIn("indicative", markdown)
            self.assertIn("failures=timeout", markdown)
            self.assertIn("warm-first", markdown)
            self.assertIn("Per-day wall p50", markdown)
            one_cold = next(
                row
                for row in report["rows"]
                if row["provider"] == "tesser"
                and row["workload"] == "one"
                and row["phase"] == "cold"
            )
            self.assertEqual(one_cold["wall_p50_ms"], 5100.0)
            self.assertEqual(one_cold["guest_compute_p50_ms"], 155.0)
            self.assertEqual(one_cold["wall_p10_ms"], 5000.0)
            self.assertEqual(one_cold["wall_p90_ms"], 5200.0)
            self.assertIn("(", markdown.split("fresh one-pass medians:", 1)[1].split("\n", 1)[0])

    def test_report_counts_failures_as_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "schema": 2,
                "pilot": True,
                "start_date": "None",
                "client_region": "us-east",
                "providers": ["modal"],
                "workloads": ["token"],
                "scenario": "standard",
                "variant": "default",
                "deadline_seconds": 120,
                "client": "test",
                "node": "26.13.1",
                "source_sha256": "failfinger",
                "seed": 1,
                "config": {"modal": {"usd_per_hour": 0.24, "price_date": "2026-11-11"}},
            }
            (root / "experiment.json").write_text(json.dumps(manifest) + "\n")
            attempts = [
                {
                    "provider": "modal",
                    "workload": "token",
                    "block": 0,
                    "scenario": "standard",
                    "started_at": "2026-11-11T12:00:00+00:00",
                    "cold": {"status": "ok", "launch_ms": 10.0, "wall_ms": 1000.0, "guest": {}},
                    "warm": {"status": "not_run"},
                    "estimated_usd": 0.001,
                    "cleanup": "removed",
                },
                {
                    "provider": "modal",
                    "workload": "token",
                    "block": 1,
                    "scenario": "standard",
                    "started_at": "2026-11-11T12:01:00+00:00",
                    "cold": {"status": "timeout", "wall_ms": 120000.0, "error": "deadline"},
                    "warm": {"status": "not_run"},
                    "estimated_usd": 0.001,
                    "cleanup": "removed",
                },
            ]
            (root / "attempts.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in attempts)
            )
            with patch("builtins.print"):
                benchpress.report(root)
            cold = next(
                row
                for row in json.loads((root / "report.json").read_text())["rows"]
                if row["phase"] == "cold"
            )
            self.assertEqual(
                cold["wall_p50_ms"], statistics.median([1000.0, benchpress.DEADLINE_MS])
            )
            warm_first = next(
                row
                for row in json.loads((root / "report.json").read_text())["rows"]
                if row["phase"] == "warm-first"
            )
            self.assertEqual(warm_first["wall_p50_ms"], benchpress.DEADLINE_MS)

    def test_burst_report_uses_manifest_count_and_deadline(self):
        manifest = {
            "pilot": True,
            "providers": ["modal", "tesser"],
            "workloads": ["token"],
            "scenario": "burst",
            "source_sha256": "burst",
            "burst_count": 2,
        }
        attempts = [
            {
                "provider": provider,
                "workload": "token",
                "block": block,
                "cold": {"status": "failed"} if block else {"status": "ok", "wall_ms": 2000},
            }
            for provider in manifest["providers"]
            for block in (1, 0)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rows, complete, deadline in ((attempts, True, 180), (attempts[:-1], False, 210)):
                with self.subTest(complete=complete, deadline=deadline):
                    manifest["deadline_seconds"] = deadline
                    (root / "experiment.json").write_text(json.dumps(manifest))
                    (root / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
                    with patch("builtins.print"):
                        benchpress.report(root)
                    report = json.loads((root / "report.json").read_text())
                    self.assertEqual(report["complete"], complete)
                    cold = next(
                        row
                        for row in report["rows"]
                        if row["provider"] == "modal" and row["phase"] == "cold"
                    )
                    deadline_ms = deadline * 1000
                    median = statistics.median([2000, deadline_ms])
                    self.assertEqual(cold["wall_p50_ms"], median)
                    self.assertEqual(cold["wall_p50_high_ms"], deadline_ms)
                    self.assertEqual(report["burst"][0]["wall"], [median, 2000, deadline_ms])
                    self.assertIn(
                        f"{deadline_ms} ms ({deadline}s deadline)", (root / "report.md").read_text()
                    )

    def test_resume_report_requires_a_verified_post_wake_token(self):
        manifest = {
            "pilot": False,
            "providers": ["modal", "namespace"],
            "workloads": ["token"],
            "scenario": "resume",
            "source_sha256": "resume",
        }
        attempts = [
            {
                "provider": "modal",
                "workload": "token",
                "block": block,
                "cold": {"status": "ok", "wall_ms": 1000},
                "resume": {"status": "ok", "wake_ms": wake_ms},
                "warm": {"status": status, "wall_ms": 20},
            }
            for block, (status, wake_ms) in enumerate((("failed", 100), ("ok", 300)))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "experiment.json").write_text(json.dumps(manifest))
            (root / "attempts.jsonl").write_text("\n".join(map(json.dumps, attempts)))
            with patch("builtins.print"):
                benchpress.report(root)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["resume"][0]["ok"], 1)
            self.assertEqual(report["resume"][0]["wake"], [300, 300, 300])
            self.assertFalse(report["resume"][1]["supported"])
            self.assertIn("1/2 verified wake/resume cycles", (root / "report.md").read_text())

    def test_report_svg_uses_the_table_intervals(self):
        manifest = {
            "pilot": False,
            "providers": ["modal"],
            "workloads": ["one"],
            "source_sha256": "intervals",
        }
        attempts = [
            {
                "provider": "modal",
                "workload": "one",
                "block": block,
                "cold": {"status": "ok", "wall_ms": wall, "guest": {"compute_ms": wall / 2}},
            }
            for block, wall in ((0, 1), (10, 10), (20, 100))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "experiment.json").write_text(json.dumps(manifest))
            (root / "attempts.jsonl").write_text("\n".join(map(json.dumps, attempts)))
            with patch("builtins.print"):
                benchpress.report(root)
            report = json.loads((root / "report.json").read_text())
            cold = next(row for row in report["rows"] if row["phase"] == "cold")
            for metric, column, expected in (
                ("wall", "wall", 10),
                ("guest-compute", "guest_compute", 5),
            ):
                with self.subTest(metric=metric):
                    self.assertEqual(cold[f"{column}_p50_low_ms"], expected)
                    self.assertEqual(cold[f"{column}_p50_high_ms"], expected)
                    svg = ElementTree.parse(root / "plots" / f"one-cold-{metric}.svg")
                    interval = next(svg.iterfind(".//{*}line[@stroke='#2563eb']"))
                    median = next(svg.iterfind(".//{*}line[@stroke-width='2']"))
                    self.assertEqual(interval.get("x1"), median.get("x1"))
                    self.assertEqual(interval.get("x2"), median.get("x1"))

    def test_trial_runs_multiple_warm_commands(self):
        calls = []

        class Box:
            version = "test"
            id = "fake-box"

            def launch(self, timeout):
                time.sleep(0.01)

            def exec(self, argv, timeout):
                calls.append(argv)
                token = argv[-1]
                if any(
                    part == benchpress.PING or (isinstance(part, str) and "BENCHPRESS_PING" in part)
                    for part in argv
                ):
                    return f"BENCHPRESS_PING {token}\n"
                if argv[1:2] == ["machine.mjs"] or (
                    len(argv) >= 2 and str(argv[1]).endswith("machine.mjs")
                ):
                    time.sleep(0.02)
                    return "BENCHPRESS_MACHINE {}\n"
                return 'BENCHPRESS_RESULT {"workload":"token","token":"%s","node":"%s"}\n' % (
                    token,
                    benchpress.NODE,
                )

            def payload(self, script, *args):
                return ["node", script, *args]

            def placement(self):
                time.sleep(0.02)
                return {}

            def close(self):
                time.sleep(0.03)
                return "removed"

        with patch("lifecycle.Sandbox", return_value=Box()):
            row = benchpress.run_mode(
                "modal", {"usd_per_hour": 36.0}, "standard", workload="token", block=0
            )
        self.assertEqual(row["cold"]["status"], "ok")
        self.assertIn("launch_ms", row["cold"])
        self.assertIn("ready_ms", row["cold"])
        self.assertIn("workload_ms", row["cold"])
        self.assertGreaterEqual(
            row["cold"]["wall_ms"],
            row["cold"]["launch_ms"] + row["cold"]["ready_ms"] + row["cold"]["workload_ms"] - 1,
        )
        self.assertEqual(row["exec_overhead"]["status"], "ok")
        self.assertEqual(len(row["exec_overhead"]["runs"]), benchpress.EXEC_PROBE_RUNS)
        self.assertEqual(row["warm"]["status"], "ok")
        self.assertEqual(len(row["warm"]["runs"]), benchpress.WARM_RUNS)
        self.assertEqual(row["warm"]["wall_first_ms"], row["warm"]["runs"][0]["wall_ms"])
        self.assertEqual(
            row["warm"]["wall_median_ms"],
            statistics.median(run["wall_ms"] for run in row["warm"]["runs"]),
        )
        self.assertGreaterEqual(len(calls), 1 + benchpress.EXEC_PROBE_RUNS + benchpress.WARM_RUNS)
        ping_calls = [argv for argv in calls if benchpress.PING in argv]
        self.assertGreaterEqual(len(ping_calls), 1 + benchpress.EXEC_PROBE_RUNS)
        self.assertLess(row["billable_seconds"], row["lifecycle_seconds"])
        self.assertGreater(row["overhead_seconds"], 0)
        self.assertAlmostEqual(
            row["estimated_usd"] + row.get("unused", 0),
            row["billable_seconds"] / 3600 * 36.0,
            places=9,
        )
        self.assertGreater(row["estimated_usd_full_lifecycle"], row["estimated_usd"])

    def test_ready_probe_retries_until_box_accepts_commands(self):
        attempts = {"n": 0}

        class Box:
            version = "test"
            id = "fake-box"

            def launch(self, timeout):
                return None

            def exec(self, argv, timeout):
                token = argv[-1]
                if benchpress.PING in argv:
                    attempts["n"] += 1
                    if attempts["n"] < 3:
                        raise RuntimeError("not ready")
                    return f"BENCHPRESS_PING {token}\n"
                return 'BENCHPRESS_RESULT {"workload":"token","token":"%s","node":"%s"}\n' % (
                    token,
                    benchpress.NODE,
                )

            def payload(self, script, *args):
                return ["node", script, *args]

            def placement(self):
                return {}

            def close(self):
                return "removed"

        with patch("lifecycle.Sandbox", return_value=Box()):
            row = benchpress.run_mode("modal", {}, "standard", workload="token", block=0)
        self.assertEqual(row["cold"]["status"], "ok")
        self.assertEqual(row["cold"]["ready_attempts"], 3)
        self.assertGreater(row["cold"]["ready_ms"], 0)

    def test_report_includes_command_send_overhead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "schema": 2,
                "pilot": True,
                "start_date": "None",
                "client_region": "us-east",
                "providers": ["tesser", "e2b"],
                "workloads": ["token"],
                "scenario": "standard",
                "variant": "default",
                "deadline_seconds": 120,
                "client": "test",
                "node": "26.13.1",
                "source_sha256": "pingfinger",
                "seed": 1,
                "config": {
                    "tesser": {"usd_per_hour": 0.08, "price_date": "2026-11-11"},
                    "e2b": {"usd_per_hour": 0.16, "price_date": "2026-11-11"},
                },
            }
            (root / "experiment.json").write_text(json.dumps(manifest) + "\n")
            attempts = []
            for provider, ping in (("tesser", 1100.0), ("e2b", 120.0)):
                attempts.append(
                    {
                        "provider": provider,
                        "workload": "token",
                        "block": 0,
                        "scenario": "standard",
                        "started_at": "2026-11-11T12:00:00+00:00",
                        "cold": {"status": "ok", "launch_ms": 50.0, "wall_ms": 2000.0, "guest": {}},
                        "exec_overhead": {
                            "status": "ok",
                            "runs": [
                                {"status": "ok", "wall_ms": ping},
                                {"status": "ok", "wall_ms": ping + 20},
                            ],
                            "wall_median_ms": ping + 10,
                            "wall_first_ms": ping,
                            "wall_min_ms": ping,
                        },
                        "warm": {"status": "ok", "wall_ms": 400.0, "guest": {}},
                        "estimated_usd": 0.001,
                        "cleanup": "removed",
                    }
                )
            (root / "attempts.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in attempts)
            )
            with patch("builtins.print"):
                benchpress.report(root)
            report = json.loads((root / "report.json").read_text())
            markdown = (root / "report.md").read_text()
            self.assertIn("## Command send overhead", markdown)
            self.assertIn("CLI process per command", markdown)
            by_provider = {row["provider"]: row for row in report["exec_overhead"]}
            self.assertEqual(by_provider["tesser"]["wall_p50_ms"], 1110.0)
            self.assertEqual(by_provider["e2b"]["wall_p50_ms"], 130.0)
            self.assertEqual(by_provider["tesser"]["wall_min_ms"], 1100.0)

    def test_resume_marks_namespace_unsupported(self):
        row = benchpress.run_mode("namespace", {}, "resume", block=0)
        self.assertEqual(row["scenario"], "resume")
        self.assertEqual(row["cold"]["status"], "unsupported")
        self.assertEqual(row["resume"]["status"], "unsupported")

    def test_burst_launches_in_parallel(self):
        started = []

        class Box:
            version = "test"
            id = "fake-box"

            def __init__(self, *args, **kwargs):
                started.append(time.monotonic())

            def launch(self, timeout):
                time.sleep(0.05)

            def exec(self, argv, timeout):
                token = argv[-1]
                if benchpress.PING in argv:
                    return f"BENCHPRESS_PING {token}\n"
                return 'BENCHPRESS_RESULT {"workload":"token","token":"%s","node":"%s"}\n' % (
                    token,
                    benchpress.NODE,
                )

            def payload(self, script, *args):
                return ["node", script, *args]

            def close(self):
                return "removed"

        with patch("lifecycle.Sandbox", side_effect=Box):
            rows = [benchpress.run_mode("modal", {}, "burst", block=index) for index in range(2)]
            from concurrent.futures import ThreadPoolExecutor

            with patch("lifecycle.Sandbox", side_effect=Box):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    parallel = list(
                        pool.map(
                            lambda index: benchpress.run_mode("modal", {}, "burst", block=index),
                            (0, 1),
                        )
                    )
        self.assertTrue(all(row["cold"]["status"] == "ok" for row in rows + parallel))
        self.assertTrue(
            all("ready_ms" in row["cold"] and "workload_ms" in row["cold"] for row in rows)
        )
        self.assertGreaterEqual(len(started), 4)

    def test_tesser_syncs_only_for_the_smoke_test(self):
        argv = ["sh", "-c", 'printf "%s\\n" "$1"', "sh", "a nonce"]
        for smoke, subcommand in ((False, "ssh"), (True, "exec")):
            with (
                self.subTest(smoke=smoke),
                patch("providers.cli", return_value="tesser"),
                patch("providers.command", return_value="[]") as command,
            ):
                box = benchpress.Sandbox("tesser", {}, "test", smoke=smoke, existing="box_abc")
                command.return_value = "a nonce\n"
                self.assertEqual(box.exec(argv, 12.5), "a nonce\n")
                command.assert_called_with(
                    ["tesser", subcommand, "box_abc", "--", *argv], timeout=12.5
                )

    def test_tesser_failed_launch_does_not_remove_a_peer_box(self):
        boxes = []
        launch_results = iter(("box_abc\n", RuntimeError("make failed")))

        def run(argv, **kwargs):
            if argv == ["tesser", "ls", "--json"]:
                return json.dumps(boxes)
            if argv[:2] == ["tesser", "make"]:
                result = next(launch_results)
                if isinstance(result, RuntimeError):
                    raise result
                boxes.append(
                    {
                        "boxId": result.strip(),
                        "worktrees": [{"root": str(benchpress.PAYLOAD.parent)}],
                    }
                )
                return result
            return ""

        with (
            patch("providers.cli", return_value="tesser"),
            patch("providers.command", side_effect=run) as command,
        ):
            settings = {"size": "standard", "snapshot": "test-snapshot"}
            peer = benchpress.Sandbox("tesser", settings, "peer")
            failed = benchpress.Sandbox("tesser", settings, "failed")
            peer.launch(10)
            with self.assertRaisesRegex(RuntimeError, "make failed"):
                failed.launch(10)
            command.reset_mock()
            self.assertEqual(failed.close(), "creation_unconfirmed")
            self.assertIsNone(failed.id)
            command.assert_not_called()
            self.assertEqual(peer.close(), "removed")
            command.assert_called_once_with(["tesser", "rm", "box_abc"])

    def test_e2b_command_supplies_the_connectrpc_stream_type(self):
        from connectrpc.method import MethodInfo, StreamType
        from e2b.envd.process import process_connect, process_pb

        with (
            patch.object(process_connect, "MethodInfo", MethodInfo),
            patch("providers.e2b_key", return_value="test"),
        ):
            benchpress.Sandbox("e2b", {}, "test")
            client = process_connect.ProcessClientSync("http://localhost")
            with patch.object(client, "execute_server_stream") as execute:
                client.start(process_pb.StartRequest())
            self.assertIs(execute.call_args.kwargs["method"].stream_type, StreamType.SERVER_STREAM)


if __name__ == "__main__":
    unittest.main()
