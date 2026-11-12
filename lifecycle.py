"""Box session: launch → ready → exec → cost → close."""

import json
import math
import os
import re
import signal
import statistics
import subprocess
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from constants import BURST_DEADLINE, DEADLINE, DEADLINE_MS, EXEC_PROBE_RUNS, WARM_RUNS
from providers import RESUME_SUPPORT, ROOT, Sandbox

NODE = (ROOT / ".node-version").read_text().strip()
PING = 'printf "BENCHPRESS_PING %s\\n" "$1"'
CHECKSUMS = {
    "one": "51fa3c6d8375a696a933cb5d5554c5606b773b2558335d0bc291173541b07b5c",
    "ten": "86ce8f3d1e87bfe6089060f88d7832a8619d0c7a34657b2d989edb4dfce80b58",
    "cores": "c0cfdc5ee4605bacdeb7038ea061f135a104ce6480d49d2f94396d569527c8be",
    "disk": "975bd791ec108ce4e0479d1111560a6d33e469f02e16c04c04efd6ee6c60a206",
    "net": "7b75c1057198cf97696909a9bee176c9c5e9bcb5b03bf3ecef2f484defadd51e",
    "install": "a4314d7ebf82c80a5a032e780e550b0f3f8e5575a3c6f7a1b52806eb1b56dcd4",
}
SMOKE = 'test "$(uname -s)" = Linux && printf "BENCHPRESS_SMOKE %s %s\\n" "$1" "$((6 * 7))"'
E2B_TOKEN_RE = re.compile(r"(?:sk_)?e2b_[A-Za-z0-9_-]{8,}")


def now():
    return datetime.now(timezone.utc).isoformat()


def error_text(error):
    message = str(error).replace(os.environ.get("E2B_API_KEY") or "\x00", "<redacted>")
    message = E2B_TOKEN_RE.sub("<redacted>", message)
    return f"{type(error).__name__}: {message}"[-1800:]


def censored_run_wall(run):
    return run["wall_ms"] if run["status"] == "ok" else DEADLINE_MS


def parse_marker(output, marker):
    lines = [line[len(marker) :] for line in output.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise ValueError(f"Expected one {marker.strip()} line, found {len(lines)}")
    return json.loads(lines[0])


def require_duration(result, *metrics):
    for metric in metrics:
        value = result.get(metric)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid guest duration: {metric}")


def verify(output, workload, token):
    result = parse_marker(output, "BENCHPRESS_RESULT ")
    expected = {"workload": workload, "token": token, "node": NODE}
    if workload in ("one", "ten"):
        expected.update(
            bytes=32 * 1024**2,
            records=524288 * (10 if workload == "ten" else 1),
            checksum=CHECKSUMS[workload],
        )
        require_duration(result, "read_ms", "compute_ms")
    elif workload == "cores":
        expected.update(
            workers=2, bytes=32 * 1024**2, records=524288 * 2, checksum=CHECKSUMS["cores"]
        )
        require_duration(result, "compute_ms")
    elif workload == "disk":
        expected.update(bytes=64 * 1024**2, checksum=CHECKSUMS["disk"])
        require_duration(result, "write_ms", "fsync_ms", "read_ms", "compute_ms")
    elif workload == "net":
        expected.update(checksum=CHECKSUMS["net"])
        require_duration(result, "download_ms", "compute_ms")
        if type(result.get("bytes")) is not int or result["bytes"] <= 0:
            raise ValueError("Invalid guest download size")
    elif workload == "install":
        expected.update(checksum=CHECKSUMS["install"])
        require_duration(result, "install_ms", "compute_ms")
    elif workload != "token":
        raise ValueError(f"Unknown workload: {workload}")
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError(f"Unexpected {key} in guest result")
    return result


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError(f"{seconds}s deadline exceeded")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def summarize_timed_runs(runs, *, guest_from_first=False):
    walls = [censored_run_wall(run) for run in runs]
    first_wall = walls[0]
    summary = {
        "status": "ok"
        if all(run["status"] == "ok" for run in runs)
        else next((run["status"] for run in runs if run["status"] != "ok"), "failed"),
        "runs": runs,
        "wall_ms": first_wall,
        "wall_first_ms": first_wall,
        "wall_median_ms": statistics.median(walls),
        "wall_min_ms": min(walls),
        "error": next((run.get("error") for run in runs if run.get("error")), None),
    }
    if guest_from_first:
        summary["guest"] = runs[0].get("guest")
    return summary


def summarize_warm(runs):
    return summarize_timed_runs(runs, guest_from_first=True)


def apply_cost(row, config, *, cold_started, billable_ended):
    row["lifecycle_seconds"] = time.monotonic() - cold_started
    row["billable_seconds"] = max(0.0, billable_ended - cold_started)
    row["overhead_seconds"] = max(0.0, row["lifecycle_seconds"] - row["billable_seconds"])
    if "usd_per_hour" in config and row.get("cleanup") == "removed":
        rate = config["usd_per_hour"]
        row["estimated_usd"] = row["billable_seconds"] / 3600 * rate
        row["estimated_usd_full_lifecycle"] = row["lifecycle_seconds"] / 3600 * rate


class Session:
    """One box lifecycle: launch → ready → exec → cost → close."""

    def __init__(
        self, provider, config, box, *, token, workload, smoke=False, use_alarm=True, budget=None
    ):
        self.provider = provider
        self.config = config
        self.box = box
        self.token = token
        self.workload = workload
        self.smoke = smoke
        self.use_alarm = use_alarm
        self.budget = DEADLINE if budget is None else budget
        self.cold_started = time.monotonic()
        self.billable_ended = self.cold_started

    @classmethod
    def connect(
        cls,
        provider,
        config,
        *,
        name,
        token,
        workload,
        smoke=False,
        existing=None,
        resume=False,
        setup_timeout=30,
        use_alarm=True,
        budget=None,
    ):
        try:
            guard = deadline(setup_timeout) if use_alarm else nullcontext()
            with guard:
                box = Sandbox(provider, config, name, smoke=smoke, existing=existing, resume=resume)
        except Exception as error:
            return None, error
        return cls(
            provider,
            config,
            box,
            token=token,
            workload=workload,
            smoke=smoke,
            use_alarm=use_alarm,
            budget=budget,
        ), None

    def _guard(self, seconds):
        return deadline(seconds) if self.use_alarm else nullcontext()

    def mark_billable(self):
        self.billable_ended = time.monotonic()

    def launch(self):
        started = time.monotonic()
        with self._guard(self.budget):
            self.box.launch(self.budget)
        return (time.monotonic() - started) * 1000

    def exec_ping(self, label, *, started=None, budget=None):
        budget = self.budget if budget is None else budget
        started = time.monotonic() if started is None else started
        sample = {"status": "running"}
        try:
            with self._guard(budget):
                nonce = f"{self.token}-ping{label}"
                output = self.box.exec(
                    ["sh", "-c", PING, "sh", nonce],
                    max(0.01, budget - (time.monotonic() - started)),
                )
                if output.splitlines().count(f"BENCHPRESS_PING {nonce}") != 1:
                    raise ValueError("Ping command did not return the expected nonce")
                sample["status"] = "ok"
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            sample.update(status="timeout", error=error_text(error))
        except Exception as error:
            sample.update(status="failed", error=error_text(error))
        finally:
            sample["wall_ms"] = (time.monotonic() - started) * 1000
        return sample

    def wait_ready(self, budget, *, skip=False):
        if skip:
            return {"ready_ms": 0.0, "ready_attempts": 0}
        started = time.monotonic()
        attempts = 0
        last_error = None
        while True:
            attempts += 1
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                message = f"{budget}s ready probe exceeded"
                if last_error:
                    message += f" ({error_text(last_error)})"
                raise TimeoutError(message)
            probe = self.exec_ping(f"ready{attempts}", started=time.monotonic(), budget=remaining)
            if probe["status"] == "ok":
                return {"ready_ms": (time.monotonic() - started) * 1000, "ready_attempts": attempts}
            last_error = probe.get("error")

    def exec_workload(self, label, *, started=None, budget=None):
        budget = self.budget if budget is None else budget
        started = time.monotonic() if started is None else started
        sample = {"status": "running"}
        try:
            with self._guard(budget):
                nonce = f"{self.token}-{label}"
                if self.smoke:
                    script = "test -f README.md && " + SMOKE if self.provider == "tesser" else SMOKE
                    argv = ["sh", "-c", script, "sh", nonce]
                else:
                    argv = self.box.payload("run.mjs", self.workload, nonce)
                remaining = max(0.01, budget - (time.monotonic() - started))
                output = self.box.exec(argv, remaining)
                if self.smoke:
                    if output.splitlines().count(f"BENCHPRESS_SMOKE {nonce} 42") != 1:
                        raise ValueError(
                            "Smoke command did not return the expected nonce and value"
                        )
                else:
                    sample["guest"] = verify(output, self.workload, nonce)
                sample["status"] = "ok"
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            sample.update(status="timeout", error=error_text(error))
        except Exception as error:
            sample.update(status="failed", error=error_text(error))
        finally:
            sample["wall_ms"] = (time.monotonic() - started) * 1000
            sample["workload_ms"] = sample["wall_ms"]
        return sample

    def run_cold(self):
        sample = {"status": "running"}
        try:
            sample["launch_ms"] = self.launch()
            remaining = max(0.01, self.budget - (time.monotonic() - self.cold_started))
            ready = self.wait_ready(remaining, skip=self.smoke)
            sample["ready_ms"] = ready["ready_ms"]
            sample["ready_attempts"] = ready["ready_attempts"]
            workload = self.exec_workload(
                "cold",
                started=time.monotonic(),
                budget=max(0.01, self.budget - (time.monotonic() - self.cold_started)),
            )
            sample["workload_ms"] = workload.get("workload_ms", workload.get("wall_ms"))
            sample["status"] = workload["status"]
            if "error" in workload:
                sample["error"] = workload["error"]
            if "guest" in workload:
                sample["guest"] = workload["guest"]
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            sample.update(status="timeout", error=error_text(error))
        except Exception as error:
            sample.update(status="failed", error=error_text(error))
        finally:
            sample["wall_ms"] = (time.monotonic() - self.cold_started) * 1000
        return sample

    def sleep_and_wake(self):
        sample = {"status": "running"}
        try:
            with self._guard(self.budget):
                sleep_started = time.monotonic()
                self.box.sleep(self.budget)
                sample["sleep_ms"] = (time.monotonic() - sleep_started) * 1000
                wake_started = time.monotonic()
                self.box.wake(self.budget)
                sample["wake_ms"] = (time.monotonic() - wake_started) * 1000
                sample["status"] = "ok"
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            sample.update(status="timeout", error=error_text(error))
        except Exception as error:
            sample.update(status="failed", error=error_text(error))
        return sample

    def collect_metadata(self, row):
        try:
            with self._guard(15):
                row["machine"] = parse_marker(
                    self.box.exec(self.box.payload("machine.mjs"), 15), "BENCHPRESS_MACHINE "
                )
                row["machine"]["region_label"] = self.config.get("region_label")
                row["machine"]["requested_site"] = self.config.get("site") or self.config.get(
                    "region"
                )
        except Exception as error:
            row["machine_error"] = error_text(error)
        try:
            with self._guard(15):
                row["provider_placement"] = self.box.placement()
        except Exception as error:
            row["placement_error"] = error_text(error)

    def close_with_cost(self, row):
        try:
            with self._guard(35):
                row["cleanup"] = self.box.close()
        except Exception as error:
            row.update(cleanup="failed", cleanup_error=error_text(error))
        row["box_id"] = self.box.id
        apply_cost(
            row, self.config, cold_started=self.cold_started, billable_ended=self.billable_ended
        )

    def _cold_ok(self, row):
        return row.get("cold", {}).get("status") == "ok"

    def _apply_step(self, row, step):
        if step.get("unless_smoke") and self.smoke:
            return True
        if step.get("if_cold_ok") and not self._cold_ok(row):
            return True
        op = step["op"]
        if op == "cold":
            row["cold"] = self.run_cold()
            field = "cold"
        elif op == "billable":
            self.mark_billable()
            return True
        elif op == "exec_overhead":
            row["exec_overhead"] = summarize_timed_runs(
                [self.exec_ping(index) for index in range(EXEC_PROBE_RUNS)]
            )
            return True
        elif op == "warm":
            row["warm"] = summarize_warm(
                [
                    self.exec_workload(f"warm{index}")
                    for index in range(step.get("count", WARM_RUNS))
                ]
            )
            return True
        elif op == "warm_once":
            warm = self.exec_workload("warm")
            row["warm"] = {"status": warm["status"], "wall_ms": warm["wall_ms"]}
            if "guest" in warm:
                row["warm"]["guest"] = warm["guest"]
            if "error" in warm:
                row["warm"]["error"] = warm["error"]
            return True
        elif op == "resume":
            row["resume"] = self.sleep_and_wake()
            field = "resume"
        elif op == "metadata":
            self.collect_metadata(row)
            return True
        else:
            raise ValueError(f"Unknown session step: {op}")
        if step.get("halt_unless_ok") and row[field]["status"] != "ok":
            return False
        return True

    def run_steps(self, row, steps):
        try:
            for step in steps:
                if not self._apply_step(row, step):
                    break
        finally:
            self.close_with_cost(row)
        return row

    @classmethod
    def run(
        cls,
        provider,
        config,
        steps,
        *,
        workload,
        block,
        scenario,
        existing=None,
        smoke=False,
        use_alarm=True,
        budget=None,
        name_prefix="benchpress",
        setup_status="setup_error",
        start_wall_before_connect=False,
    ):
        if scenario == "resume" and RESUME_SUPPORT[provider] is None:
            return {
                "provider": provider,
                "workload": "token",
                "block": block,
                "scenario": "resume",
                "started_at": now(),
                "cold": {"status": "unsupported"},
                "warm": {"status": "not_run"},
                "resume": {"status": "unsupported"},
                "cleanup": "not_run",
            }
        token = uuid.uuid4().hex
        suffix = token[:16] if start_wall_before_connect else token[:20]
        row = {
            "provider": provider,
            "workload": workload,
            "block": block,
            "name": f"{name_prefix}-{suffix}",
            "scenario": scenario,
            "started_at": now(),
            "cold": {"status": "running" if start_wall_before_connect else "not_run"},
            "warm": {"status": "not_run"},
        }
        if scenario == "standard":
            row["exec_overhead"] = {"status": "not_run"}
        if scenario == "resume":
            row["resume"] = {"status": "not_run"}
        wall_started = time.monotonic()
        session, error = cls.connect(
            provider,
            config,
            name=row["name"],
            token=token,
            workload=workload,
            smoke=smoke,
            existing=existing,
            resume=scenario == "resume",
            use_alarm=use_alarm,
            budget=budget,
        )
        if session is None:
            ended = time.monotonic()
            cold = {"status": setup_status, "error": error_text(error)}
            if start_wall_before_connect:
                cold["wall_ms"] = (ended - wall_started) * 1000
                row["cleanup"] = "not_run"
                apply_cost(row, config, cold_started=wall_started, billable_ended=ended)
            row["cold"] = cold
            return row
        if start_wall_before_connect:
            session.cold_started = wall_started
            session.billable_ended = wall_started
        row["client_version"] = session.box.version
        return session.run_steps(row, steps)


MODE_STEPS = {
    "standard": (
        {"op": "cold"},
        {"op": "billable"},
        {"op": "exec_overhead", "if_cold_ok": True, "unless_smoke": True},
        {"op": "warm", "count": WARM_RUNS, "if_cold_ok": True, "unless_smoke": True},
        {"op": "billable", "if_cold_ok": True, "unless_smoke": True},
        {"op": "metadata", "if_cold_ok": True, "unless_smoke": True},
    ),
    "resume": (
        {"op": "cold", "halt_unless_ok": True},
        {"op": "resume", "halt_unless_ok": True},
        {"op": "warm_once"},
        {"op": "billable"},
    ),
    "burst": (
        {"op": "cold"},
        {"op": "billable", "if_cold_ok": True},
    ),
}


def run_mode(provider, config, mode, *, workload=None, block=None, existing=None, smoke=False):
    """Run one attempt for a named mode using that mode's step list."""
    if mode == "standard":
        return Session.run(
            provider,
            config,
            MODE_STEPS["standard"],
            workload=workload,
            block=block,
            scenario="standard",
            existing=existing,
            smoke=smoke,
        )
    if mode == "resume":
        return Session.run(
            provider,
            config,
            MODE_STEPS["resume"],
            workload="token",
            block=block,
            scenario="resume",
        )
    if mode == "burst":
        return Session.run(
            provider,
            config,
            MODE_STEPS["burst"],
            workload="token",
            block=block,
            scenario="burst",
            use_alarm=False,
            budget=BURST_DEADLINE,
            name_prefix="benchpress-burst",
            setup_status="failed",
            start_wall_before_connect=True,
        )
    raise ValueError(f"Unknown mode: {mode}")
