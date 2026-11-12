"""Experiment modes: standard series, resume, and concurrent burst."""

import hashlib
import json
import os
import random
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from allocation import (
    AllocationJournal,
    RecoveryBlocked,
    attempt_key,
    durable_directory,
    durable_json,
    experiment_lock,
    read_rows,
    require_cleanup_resolved,
    sync_directory,
)
from constants import ARTIFACT_KEY, BURST_DEADLINE, DEADLINE, SERIES_BLOCKS, WORKLOADS
from lifecycle import NODE, run_mode
from providers import PAYLOAD, PROVIDERS, ROOT, e2b_key

SOURCE_FILES = (
    "benchpress.py",
    "allocation.py",
    "constants.py",
    "lifecycle.py",
    "scenarios.py",
    "stats.py",
    "providers.py",
    "report/__init__.py",
    "report/model.py",
    "report/render.py",
    ".node-version",
    "uv.lock",
)

MODES = ("standard", "resume", "burst")


def schedule(providers, seed, blocks=SERIES_BLOCKS, workloads=WORKLOADS):
    rng = random.Random(seed)
    for block in range(blocks):
        ordered = list(workloads)
        rng.shuffle(ordered)
        for workload in ordered:
            order = list(providers)
            rng.shuffle(order)
            for provider in order:
                yield block, workload, provider


def write_attempt(stream, row):
    stream.write(json.dumps(row, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
    sync_directory(Path(stream.name).parent)


def fingerprint():
    digest = hashlib.sha256()
    paths = [ROOT / name for name in SOURCE_FILES]
    paths.append(ROOT / ".claude/skills/tesser/benchpress.toml")
    paths += [
        path for path in PAYLOAD.rglob("*") if path.is_file() and path.name != ".benchpress-variant"
    ]
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def selected_workloads(args):
    workloads = tuple(args.workloads or WORKLOADS)
    if len(set(workloads)) != len(workloads):
        raise ValueError("List each workload at most once")
    unknown = [name for name in workloads if name not in WORKLOADS]
    if unknown:
        raise ValueError(f"Unknown workloads: {', '.join(unknown)}")
    return workloads


def open_experiment(args, config, *, mode, workloads, extra_manifest=None):
    providers = args.providers or list(PROVIDERS)
    if len(set(providers)) != len(providers):
        raise ValueError("List each provider at most once")
    for provider in providers:
        if not config[provider].get(ARTIFACT_KEY[provider]):
            raise ValueError(f"Prepare {provider} and set {ARTIFACT_KEY[provider]} in the config")
    if "e2b" in providers:
        e2b_key()
    durable_directory(args.out.resolve())
    manifest = {
        "schema": 2,
        "pilot": args.pilot,
        "start_date": str(args.start_date),
        "client_region": args.client_region,
        "providers": providers,
        "workloads": list(workloads),
        "scenario": mode,
        "variant": getattr(args, "variant", None) or "default",
        "deadline_seconds": DEADLINE,
        "client": hashlib.sha256(socket.gethostname().encode()).hexdigest(),
        "node": NODE,
        "source_sha256": fingerprint(),
        "seed": args.seed,
        "config": {name: config[name] for name in providers},
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    return providers, manifest


@contextmanager
def locked_experiment(args, manifest, *, reject_existing_attempts=False):
    manifest_path = args.out / "experiment.json"
    attempts_path = args.out / "attempts.jsonl"
    with experiment_lock(args.out):
        if manifest_path.exists():
            saved = json.loads(manifest_path.read_text())
            experiment_id = saved.get("experiment_id")
            saved_manifest = {key: value for key, value in saved.items() if key != "experiment_id"}
            if experiment_id is None:
                experiment_id = uuid.uuid4().hex
                durable_json(manifest_path, {**saved_manifest, "experiment_id": experiment_id})
            if (
                not isinstance(experiment_id, str)
                or uuid.UUID(hex=experiment_id).hex != experiment_id
            ):
                raise RecoveryBlocked("Invalid experiment ID; scheduling blocked")
            # Unresolved cleanup must win over filter/config changes so operators still see the
            # resolve-cleanup path when they narrow providers or workloads.
            recovery_manifest = {**saved_manifest, "experiment_id": experiment_id}
            journal = AllocationJournal(args.out, recovery_manifest)
            journal.recover()
            rows = read_rows(attempts_path)
            require_cleanup_resolved(rows, args.out, journal)
            if saved_manifest != manifest:
                raise ValueError("Experiment configuration changed; choose a new results directory")
            manifest["experiment_id"] = experiment_id
        else:
            experiment_id = uuid.uuid4().hex
            durable_json(manifest_path, {**manifest, "experiment_id": experiment_id})
            if (
                not isinstance(experiment_id, str)
                or uuid.UUID(hex=experiment_id).hex != experiment_id
            ):
                raise RecoveryBlocked("Invalid experiment ID; scheduling blocked")
            manifest["experiment_id"] = experiment_id
            journal = AllocationJournal(args.out, manifest)
            journal.recover()
            rows = read_rows(attempts_path)
            require_cleanup_resolved(rows, args.out, journal)
        if (
            reject_existing_attempts
            and attempts_path.exists()
            and attempts_path.read_text().strip()
        ):
            raise ValueError("Burst output already has attempts; choose a new results directory")
        yield attempts_path, journal, rows


def run_burst(args, config):
    concurrency = args.concurrency
    providers, manifest = open_experiment(
        args,
        config,
        mode="burst",
        workloads=("token",),
        extra_manifest={"burst_count": concurrency, "deadline_seconds": BURST_DEADLINE},
    )
    with locked_experiment(args, manifest) as (attempts_path, journal, rows):
        with attempts_path.open("a") as stream:
            rows = journal.reconcile_results(rows, stream, write_attempt)
            require_cleanup_resolved(rows, args.out, journal)
            if rows:
                raise ValueError(
                    "Burst output already has attempts; choose a new results directory"
                )
            for provider in providers:
                rows = []
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = [
                        pool.submit(
                            run_mode,
                            provider,
                            config[provider],
                            "burst",
                            block=index,
                            journal=journal,
                        )
                        for index in range(concurrency)
                    ]
                    for future in as_completed(futures):
                        row = future.result()
                        journal.commit(stream, row, write_attempt)
                        rows.append(row)
                        print(
                            f"burst {provider:9} {row['block']:02} {row['cold']['status']}",
                            flush=True,
                        )
                require_cleanup_resolved(rows, args.out, journal)
                if any(
                    entry.state["phase"] not in ("cleanup_confirmed", "result_committed")
                    for entry in journal.entries
                ):
                    raise RecoveryBlocked(
                        "Burst cleanup is unresolved; inspect the allocation journal before continuing"
                    )


def run_series(args, config):
    mode = args.mode
    if mode == "burst":
        return run_burst(args, config)
    workloads = ("token",) if mode == "resume" else selected_workloads(args)
    providers, manifest = open_experiment(args, config, mode=mode, workloads=workloads)
    day = 0
    if not args.pilot:
        if args.client_region != "us-east" or args.start_date is None:
            raise ValueError(
                "Use --client-region us-east and --start-date on the fixed US East client"
            )
        today = datetime.now(ZoneInfo("America/New_York")).date()
        day = (today - args.start_date).days
        if day not in range(3):
            raise ValueError("Run once per Eastern calendar day from --start-date through day 3")

    with locked_experiment(args, manifest) as (attempts_path, journal, rows):
        with attempts_path.open("a") as stream:
            rows = journal.reconcile_results(rows, stream, write_attempt)
            require_cleanup_resolved(rows, args.out, journal)
            done = {attempt_key(row) for row in rows}
            for block, workload, provider in schedule(
                providers,
                args.seed,
                1 if args.pilot else SERIES_BLOCKS,
                workloads=workloads,
            ):
                if block // 10 != day:
                    continue
                key = (block, workload if mode == "standard" else "token", provider, mode)
                if key in done:
                    continue
                if mode == "resume":
                    row = run_mode(
                        provider, config[provider], "resume", block=block, journal=journal
                    )
                else:
                    row = run_mode(
                        provider,
                        config[provider],
                        "standard",
                        workload=workload,
                        block=block,
                        journal=journal,
                    )
                journal.commit(stream, row, write_attempt)
                label = mode if mode != "standard" else workload
                print(f"{block:02} {provider:9} {label:8} {row['cold']['status']}", flush=True)
                require_cleanup_resolved([row], args.out, journal)
                if row["cold"]["status"] == "setup_error":
                    raise RuntimeError(
                        f"Inspect {attempts_path} before continuing; setup or cleanup needs attention"
                    )
