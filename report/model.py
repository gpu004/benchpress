"""Report model: aggregate attempt rows into summary structures."""

import json
import statistics
from collections import defaultdict
from datetime import date, timedelta

from constants import (
    BURST_DEADLINE,
    DEADLINE,
    EXEC_PROBE_RUNS,
    LEGACY_WORKLOADS,
    PRIMARY_WORKLOAD,
    RATIO_MIN_VERIFIED,
    SERIES_BLOCKS,
)
from providers import RESUME_SUPPORT
from stats import (
    bootstrap_median,
    censored_wall,
    interval,
    per_day_medians,
    percentile,
    ratio_ready,
    spread_ms,
    verified_command_count,
    verified_metric,
    warm_runs,
)

REPORT_PHASES = ("cold", "warm-first", "warm-median")


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def experiment_dates(rows, manifest):
    stamps = [row["started_at"][:10] for row in rows if row.get("started_at")]
    if stamps:
        return min(stamps), max(stamps)
    start = manifest.get("start_date")
    if start and start not in (None, "None"):
        end = date.fromisoformat(start) + timedelta(days=2)
        return start, end.isoformat()
    return None, None


def price_dates(manifest):
    return sorted(
        {cfg["price_date"] for cfg in manifest.get("config", {}).values() if cfg.get("price_date")}
    )


def client_region_stamp(manifest):
    region = manifest.get("client_region")
    if region in (None, "", "None"):
        return (
            "unstated (pilot or ad-hoc run)",
            "Single-client-region limitation: this report does not characterize other client regions.",
        )
    return (
        region,
        f"Single-client-region limitation: all timed attempts used the {region} client; "
        "results do not characterize other client regions.",
    )


def _phase_verified(group, phase):
    if phase == "cold":
        return sum(row["cold"].get("status") == "ok" for row in group)
    if phase == "warm-first":
        return sum(
            1
            for row in group
            if warm_runs(row.get("warm")) and warm_runs(row.get("warm"))[0].get("status") == "ok"
        )
    return sum(
        1
        for row in group
        if warm_runs(row.get("warm"))
        and all(run["status"] == "ok" for run in warm_runs(row.get("warm")))
    )


def _group_costs(group):
    verified = verified_command_count(group)
    cost_value = None
    full_cost_value = None
    if verified and all("estimated_usd" in row for row in group):
        cost_value = sum(row["estimated_usd"] for row in group) / verified
    if verified and all("estimated_usd_full_lifecycle" in row for row in group):
        full_cost_value = sum(row["estimated_usd_full_lifecycle"] for row in group) / verified
    return verified, cost_value, full_cost_value


def build_summary_rows(groups, deadline_ms):
    summary_rows = []
    for (provider, workload), group in sorted(groups.items()):
        _, cost_value, full_cost_value = _group_costs(group)
        for phase in REPORT_PHASES:
            wall_values, wall_blocks = censored_wall(group, phase, deadline_ms=deadline_ms)
            compute_phase = "warm-first" if phase.startswith("warm") else phase
            compute_values, compute_blocks = verified_metric(group, compute_phase, "compute_ms")
            wall_stats = bootstrap_median(wall_values, wall_blocks)
            compute_stats = bootstrap_median(compute_values, compute_blocks)
            wall_p90 = percentile(wall_values, 0.90) if wall_values else None
            p10, p90_spread = spread_ms(wall_values)
            day_medians = per_day_medians(wall_values, wall_blocks)
            summary_rows.append(
                {
                    "provider": provider,
                    "workload": workload,
                    "phase": phase,
                    "attempts": len(group),
                    "verified": _phase_verified(group, phase),
                    "wall_p50_ms": None if wall_stats is None else wall_stats[0],
                    "wall_p50_low_ms": None if wall_stats is None else wall_stats[1],
                    "wall_p50_high_ms": None if wall_stats is None else wall_stats[2],
                    "guest_compute_p50_ms": None if compute_stats is None else compute_stats[0],
                    "guest_compute_p50_low_ms": None if compute_stats is None else compute_stats[1],
                    "guest_compute_p50_high_ms": None
                    if compute_stats is None
                    else compute_stats[2],
                    "wall_p90_ms": wall_p90,
                    "wall_p10_ms": p10,
                    "wall_p90_spread_ms": p90_spread,
                    "per_day_wall_p50_ms": day_medians,
                    "estimated_usd_per_verified": cost_value if phase == "cold" else None,
                    "estimated_usd_full_lifecycle_per_verified": full_cost_value
                    if phase == "cold"
                    else None,
                    "wall_stats": wall_stats,
                    "compute_stats": compute_stats,
                    "wall_values": wall_values,
                    "compute_values": compute_values,
                }
            )
    return summary_rows


def build_breakdowns(groups, workloads, providers):
    breakdowns = []
    if PRIMARY_WORKLOAD not in workloads:
        return breakdowns
    for provider in providers:
        phases = [r for r in groups[provider, PRIMARY_WORKLOAD] if r["cold"]["status"] == "ok"]
        if not phases:
            continue

        def pairs(key, from_guest=False):
            out = []
            for row in phases:
                cold = row["cold"]
                if from_guest:
                    guest = cold.get("guest") or {}
                    if key in guest:
                        out.append((row["block"], guest[key]))
                elif key == "workload_ms":
                    if "workload_ms" in cold:
                        out.append((row["block"], cold["workload_ms"]))
                    elif "wall_ms" in cold:
                        inferred = (
                            cold["wall_ms"] - cold.get("launch_ms", 0) - cold.get("ready_ms", 0)
                        )
                        out.append((row["block"], max(0.0, inferred)))
                elif key in cold:
                    out.append((row["block"], cold[key]))
            return out

        launch_pairs = pairs("launch_ms")
        ready_pairs = pairs("ready_ms")
        workload_pairs = pairs("workload_ms")
        read_pairs = pairs("read_ms", from_guest=True)
        compute_pairs = pairs("compute_ms", from_guest=True)
        launch = bootstrap_median(
            [v for _, v in launch_pairs], [b for b, _ in launch_pairs] or None
        )
        ready = bootstrap_median([v for _, v in ready_pairs], [b for b, _ in ready_pairs] or None)
        workload = bootstrap_median(
            [v for _, v in workload_pairs], [b for b, _ in workload_pairs] or None
        )
        read = bootstrap_median([v for _, v in read_pairs], [b for b, _ in read_pairs] or None)
        compute = bootstrap_median(
            [v for _, v in compute_pairs], [b for b, _ in compute_pairs] or None
        )
        breakdowns.append(
            {
                "provider": provider,
                "launch": launch,
                "ready": ready,
                "workload": workload,
                "read": read,
                "compute": compute,
            }
        )
    return breakdowns


def build_cost_rows(groups):
    cost_rows = []
    for (provider, workload), group in sorted(groups.items()):
        verified = verified_command_count(group)
        if not verified:
            continue
        billable = None
        full = None
        if all("estimated_usd" in row for row in group):
            billable = sum(row["estimated_usd"] for row in group) / verified
        if all("estimated_usd_full_lifecycle" in row for row in group):
            full = sum(row["estimated_usd_full_lifecycle"] for row in group) / verified
        billable_seconds = [row["billable_seconds"] for row in group if "billable_seconds" in row]
        overhead_seconds = [row["overhead_seconds"] for row in group if "overhead_seconds" in row]
        billable_p50 = statistics.median(billable_seconds) if billable_seconds else None
        overhead_p50 = statistics.median(overhead_seconds) if overhead_seconds else None
        cost_rows.append(
            {
                "provider": provider,
                "workload": workload,
                "estimated_usd_per_verified": billable,
                "estimated_usd_full_lifecycle_per_verified": full,
                "billable_seconds_p50": billable_p50,
                "overhead_seconds_p50": overhead_p50,
            }
        )
    return cost_rows


def build_exec_overhead(rows, providers, deadline_ms):
    exec_overhead = []
    probe_rows = [row for row in rows if (row.get("exec_overhead") or {}).get("runs")]
    if not probe_rows:
        return exec_overhead
    for provider in providers:
        values, blocks = [], []
        verified = 0
        attempts = 0
        mins = []
        for row in rows:
            if row["provider"] != provider:
                continue
            overhead = row.get("exec_overhead") or {}
            runs = overhead.get("runs") or []
            if not runs and overhead.get("status") == "ok" and "wall_median_ms" in overhead:
                runs = [{"status": "ok", "wall_ms": overhead["wall_median_ms"]}]
            if not runs and overhead.get("status") not in (None, "not_run"):
                attempts += EXEC_PROBE_RUNS
                values.extend([deadline_ms] * EXEC_PROBE_RUNS)
                blocks.extend([row["block"]] * EXEC_PROBE_RUNS)
                continue
            for run in runs:
                attempts += 1
                blocks.append(row["block"])
                if run.get("status") == "ok":
                    verified += 1
                    values.append(run["wall_ms"])
                    mins.append(run["wall_ms"])
                else:
                    values.append(deadline_ms)
        if not attempts:
            continue
        stats = bootstrap_median(values, blocks)
        p10, p90 = spread_ms(values)
        minimum = min(mins) if mins else None
        exec_overhead.append(
            {
                "provider": provider,
                "attempts": attempts,
                "verified": verified,
                "wall_p50_ms": None if stats is None else stats[0],
                "wall_p50_low_ms": None if stats is None else stats[1],
                "wall_p50_high_ms": None if stats is None else stats[2],
                "wall_p10_ms": p10,
                "wall_p90_ms": p90,
                "wall_min_ms": minimum,
                "stats": stats,
            }
        )
    return exec_overhead


def build_burst_rows(rows, providers, summary_rows):
    burst_rows = []
    wall_stats = {
        row["provider"]: row["wall_stats"] for row in summary_rows if row["phase"] == "cold"
    }
    for provider in providers:
        group = [row for row in rows if row["provider"] == provider]
        verified = [row["cold"] for row in group if row["cold"].get("status") == "ok"]
        launches = [sample["launch_ms"] for sample in verified if "launch_ms" in sample]
        burst_rows.append(
            {
                "provider": provider,
                "ok": len(verified),
                "total": len(group),
                "wall": wall_stats.get(provider),
                "launch": bootstrap_median(launches),
            }
        )
    return burst_rows


def build_resume_rows(rows, providers):
    resume_rows = []
    for provider in providers:
        group = [row for row in rows if row["provider"] == provider]
        if RESUME_SUPPORT.get(provider) is None:
            resume_rows.append({"provider": provider, "supported": False})
            continue
        wakes = [
            row["resume"]["wake_ms"]
            for row in group
            if (row.get("resume") or {}).get("status") == "ok"
            and (row.get("warm") or {}).get("status") == "ok"
        ]
        resume_rows.append(
            {
                "provider": provider,
                "supported": True,
                "ok": len(wakes),
                "total": len(group),
                "wake": bootstrap_median(wakes),
            }
        )
    return resume_rows


def build_ratios(groups, workloads, manifest, complete, scenario, deadline_ms):
    ratios = []
    ratio_skips = []
    if not (
        complete
        and not manifest["pilot"]
        and scenario == "standard"
        and PRIMARY_WORKLOAD in workloads
        and "tesser" in manifest["providers"]
    ):
        return ratios, ratio_skips
    base_values, base_blocks = censored_wall(
        groups["tesser", PRIMARY_WORKLOAD], "cold", deadline_ms=deadline_ms
    )
    base = dict(zip(base_blocks, base_values))
    base_ok = sum(r["cold"].get("status") == "ok" for r in groups["tesser", PRIMARY_WORKLOAD])
    for provider in manifest["providers"]:
        if provider == "tesser":
            continue
        other_values, other_blocks = censored_wall(
            groups[provider, PRIMARY_WORKLOAD], "cold", deadline_ms=deadline_ms
        )
        other = dict(zip(other_blocks, other_values))
        other_ok = sum(r["cold"].get("status") == "ok" for r in groups[provider, PRIMARY_WORKLOAD])
        paired = {block: (other[block], base[block]) for block in base.keys() & other.keys()}
        if not ratio_ready(other_ok, base_ok, len(paired)):
            ratio_skips.append(
                {
                    "provider": provider,
                    "reason": "insufficient",
                    "other_ok": other_ok,
                    "base_ok": base_ok,
                    "paired": len(paired),
                }
            )
            continue
        left, right = zip(*paired.values())
        right_mid = statistics.median(right)
        if right_mid == 0:
            ratio_skips.append(
                {
                    "provider": provider,
                    "reason": "undefined",
                    "detail": "baseline median wall is 0",
                }
            )
            continue
        ratio = statistics.median(left) / right_mid
        low, high = interval(paired)
        if low is None or high is None:
            ratio_skips.append(
                {
                    "provider": provider,
                    "reason": "undefined",
                    "detail": "bootstrap interval undefined",
                }
            )
            continue
        ratios.append(
            {
                "provider": provider,
                "baseline": "tesser",
                "workload": PRIMARY_WORKLOAD,
                "phase": "cold",
                "ratio": ratio,
                "ratio_low": low,
                "ratio_high": high,
                "paired_blocks": len(paired),
                "verified_provider": other_ok,
                "verified_baseline": base_ok,
                "min_verified_required": RATIO_MIN_VERIFIED,
            }
        )
    return ratios, ratio_skips


def build_plot_specs(summary_rows, workloads, providers):
    specs = []
    by_group = {(row["provider"], row["workload"], row["phase"]): row for row in summary_rows}
    for workload in workloads:
        for phase in REPORT_PHASES:
            for metric, key, label in (
                ("wall", "wall", "Wall time (ms, failures=timeout)"),
                ("guest-compute", "compute", "Guest compute (ms)"),
            ):
                series = []
                for provider in providers:
                    row = by_group.get((provider, workload, phase), {})
                    values = row.get(f"{key}_values", [])
                    if values or metric == "wall":
                        series.append((provider, values, row.get(f"{key}_stats")))
                if not any(values for _, values, _ in series):
                    continue
                relative = f"plots/{workload}-{phase}-{metric}.svg"
                specs.append(
                    {
                        "relative": relative,
                        "series": series,
                        "title": f"{workload} {phase} {metric.replace('-', ' ')}",
                        "xlabel": label,
                    }
                )
    return specs


def build_report(path):
    manifest = json.loads((path / "experiment.json").read_text())
    rows = read_rows(path / "attempts.jsonl")
    workloads = tuple(manifest.get("workloads") or LEGACY_WORKLOADS)
    scenario = manifest.get("scenario", "standard")
    deadline = manifest.get("deadline_seconds", BURST_DEADLINE if scenario == "burst" else DEADLINE)
    deadline_ms = deadline * 1000
    groups = defaultdict(list)
    for row in rows:
        groups[row["provider"], row["workload"]].append(row)
    target = 1 if manifest["pilot"] else SERIES_BLOCKS
    if scenario == "burst":
        target = manifest.get("burst_count")
    expected_blocks = list(range(target)) if target is not None else []
    complete = bool(expected_blocks) and all(
        sorted(r["block"] for r in groups[p, w]) == expected_blocks
        for p in manifest["providers"]
        for w in workloads
    )
    start_day, end_day = experiment_dates(rows, manifest)
    prices = price_dates(manifest)
    region_value, region_note = client_region_stamp(manifest)
    summary_rows = build_summary_rows(groups, deadline_ms)
    breakdowns = build_breakdowns(groups, workloads, manifest["providers"])
    cost_rows = (
        build_cost_rows(groups)
        if any("estimated_usd" in row or "estimated_usd_full_lifecycle" in row for row in rows)
        else []
    )
    exec_overhead = build_exec_overhead(rows, manifest["providers"], deadline_ms)
    burst_rows = (
        build_burst_rows(rows, manifest["providers"], summary_rows) if scenario == "burst" else []
    )
    resume_rows = build_resume_rows(rows, manifest["providers"]) if scenario == "resume" else []
    ratios, ratio_skips = build_ratios(groups, workloads, manifest, complete, scenario, deadline_ms)
    plot_specs = build_plot_specs(summary_rows, workloads, manifest["providers"])
    return {
        "path": path,
        "manifest": manifest,
        "rows": rows,
        "workloads": workloads,
        "scenario": scenario,
        "complete": complete,
        "start_day": start_day,
        "end_day": end_day,
        "prices": prices,
        "region_value": region_value,
        "region_note": region_note,
        "summary_rows": summary_rows,
        "breakdowns": breakdowns,
        "cost_rows": cost_rows,
        "exec_overhead": exec_overhead,
        "burst_rows": burst_rows,
        "resume_rows": resume_rows,
        "ratios": ratios,
        "ratio_skips": ratio_skips,
        "plot_specs": plot_specs,
        "deadline": deadline,
        "deadline_ms": deadline_ms,
    }
