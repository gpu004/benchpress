import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
PROVIDERS = ("tesser", "modal", "e2b", "namespace")
VARIANTS = ("default", "large")
TTL = 360
RESUME_SUPPORT = {
    "tesser": "sleep_wake",
    "modal": "experimental_pause_resume",
    "e2b": "pause_connect",
    "namespace": None,
}


def command(argv, *, timeout=30, cwd=ROOT, capture=True, env=None):
    result = subprocess.run(
        argv,
        cwd=cwd,
        timeout=timeout,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=capture,
        env=env,
    )
    if result.returncode:
        message = (result.stderr or result.stdout or "").strip()[-1600:]
        raise RuntimeError(f"{Path(argv[0]).name} exited {result.returncode}: {message}")
    return result.stdout or ""


def cli(name):
    binary = shutil.which(name)
    if name == "tesser" and not binary:
        candidate = Path.home() / ".tesser/bin/tesser"
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        raise RuntimeError(f"Install the {name} CLI first")
    return binary


def e2b_key():
    if key := os.environ.get("E2B_API_KEY"):
        return key
    try:
        config = json.loads((Path.home() / ".e2b/config.json").read_text())
    except OSError, ValueError:
        config = {}
    key = config.get("projectApiKey") if isinstance(config, dict) else None
    if isinstance(key, str) and key:
        return key
    raise RuntimeError("Run e2b auth login or set E2B_API_KEY in the client environment")


def _patch_e2b_process_method_info():
    # E2B 2.63.0's generated process client omits the stream type required by connectrpc 0.13.
    from connectrpc.method import MethodInfo, StreamType
    from e2b.envd.process import process_connect

    if process_connect.MethodInfo is not MethodInfo:
        return
    stream_types = {
        "unary": StreamType.UNARY,
        "client_streaming": StreamType.CLIENT_STREAM,
        "server_streaming": StreamType.SERVER_STREAM,
        "bidi_streaming": StreamType.BIDI_STREAM,
    }
    methods = {
        method.name: stream_types[method.method_kind]
        for method in process_connect.ProcessSync.desc().methods
    }

    def method_info(*, name, **kwargs):
        return MethodInfo(name=name, stream_type=methods[name], **kwargs)

    process_connect.MethodInfo = method_info


class Sandbox:
    def __init__(self, provider, settings, name, *, smoke=False, existing=None, resume=False):
        self.provider, self.settings, self.name = provider, settings, name
        self.existing = existing
        self.id = existing
        self.handle = None
        self.before = set()
        self.smoke = smoke
        self.resume = resume
        if provider == "tesser":
            self.binary = cli("tesser")
            self.version = command([self.binary, "--version"]).strip()
            command(["git", "rev-parse", "--verify", "HEAD"])
            self.before = {box["boxId"] for box in self.tesser_boxes()}
        elif provider == "namespace":
            self.binary = cli("devbox")
            self.version = command([self.binary, "version"]).strip()
        elif provider == "modal":
            import modal

            self.modal = modal
            self.version = modal.__version__
            self.app = modal.App.lookup(settings["app"], create_if_missing=smoke)
            self.image = (
                modal.Image.debian_slim() if smoke else modal.Image.from_id(settings["image"])
            )
        else:
            from importlib.metadata import version

            import e2b

            _patch_e2b_process_method_info()
            self.e2b_api_key = e2b_key()
            self.e2b = e2b
            self.version = version("e2b")

    def tesser_boxes(self):
        return json.loads(command([self.binary, "ls", "--json"]))

    def launch(self, timeout):
        if self.existing:
            return
        settings = self.settings
        if self.provider == "tesser":
            args = [self.binary, "make", "--size", settings["size"]]
            args += ["--no-snapshot"] if self.smoke else ["--snapshot", settings["snapshot"]]
            box = command(args, timeout=timeout).strip()
            if not re.fullmatch(r"box_[0-9a-f]+", box) or box in self.before:
                raise RuntimeError("Tesser did not return a new box ID")
            self.id = box
        elif self.provider == "namespace":
            args = [
                self.binary,
                "create",
                "--name",
                self.name,
                "--platform",
                "linux/amd64",
                "--size",
                settings["size"],
                "--ephemeral",
                "--no_checkout",
                "--access_mode",
                "private",
                "--auto_stop_idle_timeout",
                "5m",
                "--image",
                "builtin:default" if self.smoke else settings["image"],
            ]
            if site := settings.get("site"):
                args += ["--site", site]
            command(args, timeout=timeout)
            self.id = self.name
        elif self.provider == "modal":
            options = {"region": settings["region"]} if settings.get("region") else {}
            self.handle = self.modal.Sandbox.create(
                app=self.app,
                image=self.image,
                name=None if self.resume else self.name,
                runtime="gvisor",
                cpu=(1.0, 1.0),
                memory=(4096, 4096),
                timeout=None if self.resume else TTL,
                _experimental_persistent=self.resume,
                **options,
            )
            self.id = self.handle.object_id
        else:
            self.handle = self.e2b.Sandbox.create(
                **({} if self.smoke else {"template": settings["template"]}),
                metadata={"benchpress_trial": self.name},
                timeout=TTL,
                request_timeout=timeout,
                api_key=self.e2b_api_key,
            )
            self.id = self.handle.sandbox_id

    def exec(self, argv, timeout):
        if self.provider == "tesser":
            if self.smoke:
                return command([self.binary, "exec", self.id, "--", *argv], timeout=timeout)
            return command([self.binary, "ssh", self.id, "--", *argv], timeout=timeout)
        if self.provider == "namespace":
            return command([self.binary, "exec", self.id, "--", *argv], timeout=timeout)
        if self.provider == "modal":
            proc = self.handle.exec(*argv, timeout=max(1, math.ceil(timeout)))
            output = proc.stdout.read()
            if proc.wait():
                raise RuntimeError(
                    f"Modal command exited {proc.returncode}: {proc.stderr.read()[-1600:]}"
                )
            return output
        seconds = max(1, math.ceil(timeout))
        result = self.handle.commands.run(
            shlex.join(argv), timeout=seconds, request_timeout=seconds
        )
        return result.stdout

    def payload(self, script, *args):
        if self.provider == "tesser":
            script_command = (
                'script=$1; shift; exec "$HOME/.benchpress/node" "$HOME/.benchpress/$script" "$@"'
            )
            return ["sh", "-c", script_command, "sh", script, *args]
        return ["/opt/benchpress/node", f"/opt/benchpress/{script}", *args]

    def placement(self):
        if self.provider == "namespace":
            boxes = json.loads(command([self.binary, "list", "--output", "json"], timeout=15))
            box = next(b for b in boxes if b["name"] == self.id)
            return {"site": box.get("site"), "shape": box.get("instance_shape")}
        if self.provider == "tesser":
            box = json.loads(command([self.binary, "status", self.id, "--json"], timeout=15))
            return {key: box[key] for key in ("region", "site", "size", "fleet") if key in box}
        if self.provider == "e2b":
            info = self.handle.get_info(request_timeout=10)
            return {
                "vcpu": info.cpu_count,
                "memory_mib": info.memory_mb,
                "domain": info.sandbox_domain,
            }
        return {}

    def resume_kind(self):
        return RESUME_SUPPORT[self.provider]

    def sleep(self, timeout):
        kind = self.resume_kind()
        if kind is None:
            raise RuntimeError(f"{self.provider} does not support snapshot/resume in this harness")
        if self.provider == "tesser":
            command([self.binary, "sleep", self.id, "--yes"], timeout=timeout)
            return
        if self.provider == "modal":
            self.handle._experimental_pause()
            return
        self.handle.pause(mode="full", request_timeout=timeout)

    def wake(self, timeout):
        kind = self.resume_kind()
        if kind is None:
            raise RuntimeError(f"{self.provider} does not support snapshot/resume in this harness")
        if self.provider == "tesser":
            command([self.binary, "wake", self.id], timeout=timeout)
            return
        if self.provider == "modal":
            self.handle._experimental_resume()
            return
        self.handle = self.e2b.Sandbox.connect(
            self.id,
            timeout=TTL,
            on_resume="restore",
            request_timeout=timeout,
            api_key=self.e2b_api_key,
        )

    def close(self):
        if self.existing:
            return "existing_box_kept"
        if self.provider == "tesser":
            if not self.id:
                # A box listed after a failed launch may belong to another worker.
                return "creation_unconfirmed"
            command([self.binary, "rm", self.id])
        elif self.provider == "namespace":
            try:
                command([self.binary, "expire", self.name, "--force"])
            except RuntimeError as error:
                if "code = NotFound" in str(error):
                    return "creation_unconfirmed"
                raise
            self.id = self.name
        elif self.provider == "modal":
            if not self.handle:
                if self.resume:
                    # The SDK cannot look up persistent sandboxes after an unconfirmed create.
                    return "creation_unconfirmed"
                try:
                    self.handle = self.modal.Sandbox.from_name(self.settings["app"], self.name)
                except self.modal.exception.NotFoundError:
                    return "creation_unconfirmed"
                self.id = self.handle.object_id
            self.handle.terminate(wait=True)
        else:
            if not self.handle:
                from e2b.sandbox.sandbox_api import SandboxQuery

                matches = self.e2b.Sandbox.list(
                    query=SandboxQuery(metadata={"benchpress_trial": self.name}),
                    limit=2,
                    request_timeout=15,
                    api_key=self.e2b_api_key,
                ).next_items()
                if len(matches) != 1:
                    return "creation_unconfirmed"
                self.id = matches[0].sandbox_id
            self.e2b.Sandbox.kill(self.id, request_timeout=15, api_key=self.e2b_api_key)
        return "removed"


def _tesser_snapshot(cwd):
    output = command([cli("tesser"), "snapshot", "create", "--wait"], cwd=cwd, timeout=1200)
    return output.strip().splitlines()[-1].split()[0]


def _prepare_large_tesser(name):
    # Tesser uploads the selected Git tree. Commit the marker in a private clone so
    # the snapshot stays associated with this repo without modifying its checkout.
    origin = command(["git", "remote", "get-url", "origin"], cwd=ROOT).strip()
    with TemporaryDirectory(prefix="benchpress-tesser-") as tmp:
        command(["git", "clone", "--quiet", "--shared", "--origin", "origin", str(ROOT), tmp])
        if (Path(tmp) / "payload/prepare.sh").read_bytes() != (PAYLOAD / "prepare.sh").read_bytes():
            raise ValueError("Commit payload/prepare.sh before preparing a large Tesser snapshot")
        command(["git", "remote", "set-url", "origin", origin], cwd=tmp)
        command(["git", "switch", "--quiet", "-c", name], cwd=tmp)
        marker = "payload/.benchpress-variant"
        (Path(tmp) / marker).write_text("large\n")
        command(["git", "add", "--force", "--", marker], cwd=tmp)
        command(
            [
                "git",
                "-c",
                "user.name=Benchpress",
                "-c",
                "user.email=benchpress@localhost",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--quiet",
                "-m",
                "Prepare large Benchpress payload",
            ],
            cwd=tmp,
        )
        return _tesser_snapshot(tmp)


def prepare(provider, settings, name, *, variant="default"):
    if variant not in VARIANTS:
        raise ValueError(f"Unknown prepare variant: {variant}")
    if provider == "tesser":
        snapshot = _tesser_snapshot(ROOT) if variant == "default" else _prepare_large_tesser(name)
        return {"snapshot": snapshot, "variant": variant}
    with TemporaryDirectory(prefix="benchpress-image-") as tmp:
        payload = Path(tmp) / PAYLOAD.name
        shutil.copytree(PAYLOAD, payload)
        (payload / ".benchpress-variant").write_text(variant + "\n")
        if provider == "namespace":
            command(
                [cli("devbox"), "image", "build", str(payload), "--name", name, "--user", "root"],
                timeout=1200,
                capture=False,
            )
            return {"image": name, "variant": variant}
        if provider == "modal":
            import modal

            app = modal.App.lookup(settings["app"], create_if_missing=True)
            image = modal.Image.from_registry("python:3.15.2-slim-trixie").apt_install(
                "curl",
                "xz-utils",
                "libatomic1",
            )
            image = image.add_local_dir(payload, "/tmp/benchpress-payload", copy=True).run_commands(
                "sh /tmp/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /tmp/benchpress-payload",
            )
            image = image.build(app)
            return {"image": image.object_id, "variant": variant}
        from e2b import Template

        api_key = e2b_key()
        # E2B clears /tmp between COPY and RUN layers, so stage under /opt.
        # Copy install-package to its own dest so the directory name is kept
        # (COPY dir dest/ otherwise flattens contents into dest).
        files = [
            "prepare.sh",
            "fixture.mjs",
            "run.mjs",
            "machine.mjs",
            "cores-worker.mjs",
            ".benchpress-variant",
        ]
        template = (
            Template(file_context_path=payload)
            .from_image("node:26.13.1-trixie-slim")
            .copy(files, "/opt/benchpress-payload/", user="root")
            .copy(
                "install-package",
                "/opt/benchpress-payload/install-package/",
                user="root",
            )
            .run_cmd(
                "sh /opt/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /opt/benchpress-payload",
                user="root",
            )
        )
        build = Template.build(template, name=name, cpu_count=2, memory_mb=4096, api_key=api_key)
        return {"template": build.template_id, "variant": variant}
