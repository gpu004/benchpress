import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

import modal

import providers
from lifecycle import NODE, PING, run_mode


class ModalSandboxTests(unittest.TestCase):
    def test_resume_mode_creates_and_terminates_a_persistent_sandbox(self):
        def exec_command(*argv, **kwargs):
            token = argv[-1]
            output = (
                f"BENCHPRESS_PING {token}"
                if PING in argv
                else "BENCHPRESS_RESULT "
                + json.dumps({"workload": "token", "token": token, "node": NODE})
            )
            process = Mock()
            process.stdout.read.return_value = output
            process.wait.return_value = 0
            return process

        handle = Mock(object_id="sb-test")
        handle.exec.side_effect = exec_command
        with (
            patch.object(modal.App, "lookup"),
            patch.object(modal.Image, "from_id"),
            patch.object(modal.Sandbox, "create", return_value=handle) as create,
        ):
            settings = {"app": "test-app", "image": "im-test"}
            row = run_mode("modal", settings, "resume", block=0)
            options = create.call_args.kwargs
            self.assertTrue(options["_experimental_persistent"])
            self.assertIsNone(options["timeout"])
            self.assertIsNone(options.get("name"))
            for phase in ("cold", "resume", "warm"):
                self.assertEqual(row[phase]["status"], "ok", row[phase])
            self.assertEqual(row["cleanup"], "removed")
            handle._experimental_pause.assert_called_once_with()
            handle._experimental_resume.assert_called_once_with()
            handle.terminate.assert_called_once_with(wait=True)

            box = providers.Sandbox("modal", settings, "ordinary-trial")
            box.launch(30)
            self.assertEqual(create.call_args.kwargs["name"], "ordinary-trial")
            self.assertEqual(create.call_args.kwargs["timeout"], providers.TTL)
            self.assertFalse(create.call_args.kwargs.get("_experimental_persistent"))

    def test_unconfirmed_persistent_create_skips_name_lookup(self):
        with (
            patch.object(modal.App, "lookup"),
            patch.object(modal.Image, "from_id"),
            patch.object(modal.Sandbox, "create", side_effect=TimeoutError),
            patch.object(modal.Sandbox, "from_name") as lookup,
        ):
            settings = {"app": "test-app", "image": "im-test"}
            row = run_mode("modal", settings, "resume", block=0)
        self.assertEqual(row["cold"]["status"], "timeout")
        self.assertEqual(row["cleanup"], "creation_unconfirmed")
        lookup.assert_not_called()


class PrepareTests(unittest.TestCase):
    def test_overlapping_prepares_keep_their_own_variant_until_build_finishes(self):
        large_started = Event()
        default_finished = Event()
        contexts = {}

        def build(argv, **kwargs):
            context, name = Path(argv[3]), argv[5]
            contexts[name] = context
            if name == "large":
                large_started.set()
                self.assertTrue(default_finished.wait(5))
            self.assertEqual((context / ".benchpress-variant").read_text().strip(), name)

        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "payload"
            source.mkdir()
            with (
                patch.object(providers, "PAYLOAD", source),
                patch.object(providers, "cli", return_value="devbox"),
                patch.object(providers, "command", side_effect=build),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                large = pool.submit(providers.prepare, "namespace", {}, "large", variant="large")
                try:
                    self.assertTrue(large_started.wait(5))
                    default = providers.prepare("namespace", {}, "default")
                    self.assertEqual(default["variant"], "default")
                finally:
                    default_finished.set()
                self.assertEqual(large.result()["variant"], "large")
                self.assertFalse((source / ".benchpress-variant").exists())
        self.assertNotEqual(contexts["large"], contexts["default"])
        self.assertTrue(all(not context.exists() for context in contexts.values()))

    def test_large_tesser_snapshot_reads_the_marker_from_a_private_commit(self):
        origin = "https://github.com/example/benchpress.git"
        git = providers.command

        def snapshot(cwd):
            self.assertNotEqual(Path(cwd), root)
            self.assertEqual(git(["git", "remote", "get-url", "origin"], cwd=cwd).strip(), origin)
            marker = git(["git", "show", "HEAD:payload/.benchpress-variant"], cwd=cwd)
            self.assertEqual(marker, "large\n")
            self.assertEqual(git(["git", "rev-parse", "HEAD^"], cwd=cwd).strip(), head)
            return "snap-test"

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload"
            payload.mkdir()
            (payload / "prepare.sh").write_text("#!/bin/sh\n")
            git(["git", "init", "--quiet", "--initial-branch=main"], cwd=root)
            git(["git", "remote", "add", "origin", origin], cwd=root)
            git(["git", "add", "payload"], cwd=root)
            git(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "commit",
                    "--no-gpg-sign",
                    "-qm",
                    "Fixture",
                ],
                cwd=root,
            )
            head = git(["git", "rev-parse", "HEAD"], cwd=root).strip()
            with (
                patch.object(providers, "ROOT", root),
                patch.object(providers, "PAYLOAD", payload),
                patch.object(providers, "_tesser_snapshot", side_effect=snapshot) as build,
            ):
                result = providers.prepare("tesser", {}, "benchpress-test", variant="large")
            self.assertEqual(result, {"snapshot": "snap-test", "variant": "large"})
            self.assertEqual(git(["git", "rev-parse", "HEAD"], cwd=root).strip(), head)
            self.assertEqual(git(["git", "status", "--porcelain"], cwd=root), "")
            self.assertFalse(Path(build.call_args.args[0]).exists())
