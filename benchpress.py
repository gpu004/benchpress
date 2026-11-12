"""CLI for preparing, running, and reporting sandbox benchmarks."""

import argparse
import json
import sys
import time
import tomllib
import uuid
from datetime import date
from pathlib import Path

from allocation import RecoveryBlocked, reconcile_manual, resolve_cleanup
from constants import DEADLINE_MS as DEADLINE_MS
from constants import EXEC_PROBE_RUNS as EXEC_PROBE_RUNS
from constants import RATIO_MIN_VERIFIED as RATIO_MIN_VERIFIED
from constants import SERIES_BLOCKS as SERIES_BLOCKS
from constants import WARM_RUNS as WARM_RUNS
from constants import WORKLOADS
from lifecycle import NODE as NODE
from lifecycle import PING as PING
from lifecycle import Session as Session
from lifecycle import error_text, run_mode
from lifecycle import verify as verify
from providers import PAYLOAD as PAYLOAD
from providers import PROVIDERS, ROOT, VARIANTS, prepare
from providers import Sandbox as Sandbox
from report import report
from scenarios import MODES, fingerprint, run_series
from scenarios import schedule as schedule
from stats import bootstrap_median as bootstrap_median
from stats import interval as interval
from stats import ratio_ready as ratio_ready


def settings(path):
    config = tomllib.loads((ROOT / "benchpress.toml").read_text())
    if path:
        for provider, overrides in tomllib.loads(Path(path).read_text()).items():
            if provider not in PROVIDERS:
                raise ValueError(f"Unknown provider in config: {provider}")
            config[provider].update(overrides)
    return config


def main():
    parser = argparse.ArgumentParser(
        description="Compare quick remote execution on isolated cloud boxes"
    )
    parser.add_argument("--config", type=Path, help="TOML overrides for providers")
    sub = parser.add_subparsers(dest="action", required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("provider", choices=PROVIDERS)
    smoke.add_argument("--existing", help="Run against an existing Namespace box")
    prep = sub.add_parser("prepare")
    prep.add_argument("provider", choices=PROVIDERS)
    prep.add_argument(
        "--variant",
        choices=VARIANTS,
        default="default",
        help="default payload, or large with a 256 MiB filler for pull/restore cost",
    )
    run = sub.add_parser("run")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--providers", nargs="+", choices=PROVIDERS)
    run.add_argument(
        "--workloads",
        nargs="+",
        choices=WORKLOADS,
        help="subset of workloads; default is all, including cores/disk/net/install",
    )
    run.add_argument(
        "--mode",
        choices=MODES,
        default="standard",
        help="standard cold/warm, resume sleep/wake, or concurrent burst launches",
    )
    run.add_argument(
        "--concurrency",
        type=int,
        metavar="N",
        help="boxes launched at once per provider when --mode burst",
    )
    run.add_argument(
        "--variant",
        choices=VARIANTS,
        default="default",
        help="record which prepared image variant this experiment uses",
    )
    run.add_argument("--pilot", action="store_true")
    run.add_argument("--client-region")
    run.add_argument("--start-date", type=date.fromisoformat)
    run.add_argument("--seed", type=int, default=7256)
    sub.add_parser("report").add_argument("path", type=Path)
    resolve = sub.add_parser(
        "resolve-cleanup", help="Record manual evidence of an exact allocation's terminal outcome"
    )
    resolve.add_argument("--out", type=Path, required=True)
    resolve.add_argument("--attempt", required=True, help="Full attempt ID from the cleanup error")
    resolve.add_argument("--resolved-by", required=True, help="Operator who verified the outcome")
    resolve.add_argument(
        "--allocation-scope", required=True, help="Verified account and allocation"
    )
    resolve.add_argument(
        "--evidence", required=True, help="Evidence of ownership and terminal state"
    )
    resolve.add_argument("--confirmed-outcome", choices=("removed", "absent"), required=True)
    reconcile = sub.add_parser(
        "reconcile", help="Record evidence that an allocation and its create are terminal"
    )
    reconcile.add_argument("--out", type=Path, required=True)
    reconcile.add_argument("--allocation", required=True)
    reconcile.add_argument("--confirm-terminal", action="store_true", required=True)
    reconcile.add_argument("--actor", required=True)
    reconcile.add_argument("--evidence", required=True)
    reconcile.add_argument("--allocation-scope", help="Verified provider account and allocation")
    reconcile.add_argument(
        "--confirmed-outcome", choices=("removed", "absent", "terminal"), default="terminal"
    )
    args = parser.parse_args()
    try:
        if args.action == "resolve-cleanup":
            receipt = resolve_cleanup(
                args.out,
                args.attempt,
                resolved_by=args.resolved_by,
                allocation_scope=args.allocation_scope,
                evidence=args.evidence,
                confirmed_outcome=args.confirmed_outcome,
            )
            print(json.dumps(receipt, indent=2))
            return 0
        if args.action == "reconcile":
            receipt = reconcile_manual(
                args.out,
                args.allocation,
                actor=args.actor,
                evidence=args.evidence,
                allocation_scope=args.allocation_scope,
                confirmed_outcome=args.confirmed_outcome,
            )
            print(json.dumps(receipt, indent=2))
            return 0
        config = settings(args.config)
        if args.action == "report":
            report(args.path)
        elif args.action == "smoke":
            if args.existing and args.provider != "namespace":
                raise ValueError("--existing is for Namespace")
            row = run_mode(
                args.provider,
                config[args.provider],
                "standard",
                workload="smoke",
                block=None,
                existing=args.existing,
                smoke=True,
            )
            print(json.dumps(row, indent=2))
            return int(
                row["cold"]["status"] != "ok"
                or row.get("cleanup") not in ("removed", "existing_box_kept")
            )
        elif args.action == "prepare":
            started = time.monotonic()
            name = f"benchpress-{fingerprint()[:10]}-{uuid.uuid4().hex[:8]}"
            result = prepare(args.provider, config[args.provider], name, variant=args.variant)
            print(
                json.dumps(
                    {
                        "provider": args.provider,
                        **result,
                        "prepare_seconds": time.monotonic() - started,
                    }
                )
            )
        else:
            if args.mode == "burst":
                if args.concurrency is None or args.concurrency < 2:
                    raise ValueError("--mode burst requires --concurrency N with N >= 2")
                if args.workloads:
                    raise ValueError("--workloads is only for --mode standard")
            elif args.concurrency is not None:
                raise ValueError("--concurrency is only valid with --mode burst")
            run_series(args, config)
    except (ValueError, OSError, RuntimeError, RecoveryBlocked) as error:
        print(error_text(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
