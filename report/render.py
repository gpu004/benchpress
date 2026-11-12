"""Report renderers: markdown, JSON, CSV, and distribution SVGs."""

import csv
import html
import json

from constants import (
    EXEC_PROBE_RUNS,
    PRIMARY_WORKLOAD,
    RATIO_MIN_VERIFIED,
    WARM_RUNS,
)


def format_ms(value):
    return "—" if value is None else f"{value:.2f}"


def format_median(stats):
    if stats is None:
        return "—"
    mid, low, high = stats
    return f"{mid:.2f} ({low:.2f}–{high:.2f})"


def format_spread(low, high):
    if low is None or high is None:
        return "—"
    return f"{low:.2f}–{high:.2f}"


def format_day_medians(day_medians):
    if not day_medians:
        return "—"
    return " / ".join(f"d{day} {value:.2f}" for day, value in day_medians.items())


def write_distribution_svg(path, series, title, xlabel):
    width, left, right, top, row_height, mark = 720, 120, 36, 48, 44, 5
    height = top + row_height * max(len(series), 1) + 40
    finite = [value for _, values, _ in series for value in values]
    low = min(finite) if finite else 0
    high = max(finite) if finite else 1
    if high <= low:
        high = low + 1
    span = high - low

    def x_for(value):
        return left + (value - low) / span * (width - left - right)

    ticks = [low + span * fraction for fraction in (0, 0.25, 0.5, 0.75, 1)]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="{left}" y="24" font-family="ui-sans-serif, system-ui, sans-serif" '
        f'font-size="16" fill="#111">{html.escape(title)}</text>',
        f'<text x="{(left + width - right) / 2}" y="{height - 12}" text-anchor="middle" '
        f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="12" fill="#444">'
        f"{html.escape(xlabel)}</text>",
    ]
    for tick in ticks:
        x = x_for(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 36}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{height - 24}" text-anchor="middle" '
            f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="11" fill="#666">'
            f"{tick:.0f}</text>"
        )
    for index, (label, values, stats) in enumerate(series):
        y = top + index * row_height + row_height / 2
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="#bbb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="12" fill="#222">'
            f"{html.escape(label)}</text>"
        )
        if stats is None:
            continue
        mid, lo, hi = stats
        parts.append(
            f'<line x1="{x_for(lo):.1f}" y1="{y:.1f}" x2="{x_for(hi):.1f}" y2="{y:.1f}" '
            f'stroke="#2563eb" stroke-width="3" stroke-linecap="round"/>'
        )
        parts.append(
            f'<line x1="{x_for(mid):.1f}" y1="{y - 10:.1f}" x2="{x_for(mid):.1f}" '
            f'y2="{y + 10:.1f}" stroke="#111" stroke-width="2"/>'
        )
        for value in values:
            parts.append(
                f'<circle cx="{x_for(value):.1f}" cy="{y:.1f}" r="{mark}" '
                f'fill="#111" fill-opacity="0.55"/>'
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n")


def breakdown_line(item):
    read, compute = item["read"], item["compute"]
    return (
        f"- {item['provider']} fresh one-pass medians: launch call {format_median(item['launch'])} ms, "
        f"time to ready {format_median(item['ready'])} ms, workload {format_median(item['workload'])} ms"
        + (
            f", guest read {format_median(read)} ms, guest compute {format_median(compute)} ms"
            if read and compute
            else ""
        )
        + "."
    )


def exec_overhead_line(entry):
    return (
        f"- {entry['provider']} command-send overhead: p50 {format_median(entry['stats'])} ms "
        f"(p10–p90 {format_spread(entry['wall_p10_ms'], entry['wall_p90_ms'])}, "
        f"min {format_ms(entry['wall_min_ms'])}, "
        f"{entry['verified']}/{entry['attempts']} verified pings)."
    )


def burst_line(row):
    return (
        f"- {row['provider']} had {row['ok']}/{row['total']} verified concurrent launches. "
        f"Wall p50 was {format_median(row['wall'])} ms. "
        f"Launch p50 among verified attempts was {format_median(row['launch'])} ms."
    )


def resume_line(row):
    if not row["supported"]:
        return f"- {row['provider']}: resume unsupported in this harness."
    return (
        f"- {row['provider']} had {row['ok']}/{row['total']} verified wake/resume cycles. "
        f"Wake p50 among verified cycles was {format_median(row['wake'])} ms."
    )


def ratio_line(entry, deadline):
    return (
        f"- {entry['provider']} / Tesser, fresh one-pass (failures counted as {deadline}s): "
        f"{entry['ratio']:.2f}× (95% interval {entry['ratio_low']:.2f}–{entry['ratio_high']:.2f}×, "
        f"{entry['paired_blocks']} paired blocks, "
        f"{entry['verified_provider']}/{entry['verified_baseline']} verified)."
    )


def ratio_skip_line(skip):
    if skip["reason"] == "insufficient":
        return (
            f"- {skip['provider']} / Tesser: insufficient verified pairs for a headline ratio "
            f"(need ≥{RATIO_MIN_VERIFIED} verified on each side and ≥{RATIO_MIN_VERIFIED} "
            f"paired blocks; have {skip['other_ok']}, {skip['base_ok']}, {skip['paired']})."
        )
    return f"- {skip['provider']} / Tesser: ratio undefined ({skip['detail']})."


def render_markdown(model):
    manifest = model["manifest"]
    start_day, end_day = model["start_day"], model["end_day"]
    prices = model["prices"]
    price_stamp = ", ".join(prices) if prices else "unknown"
    date_stamp = (
        f"{start_day} to {end_day}"
        if start_day and end_day and start_day != end_day
        else start_day or end_day or "unknown"
    )
    lines = ["# Benchpress results", ""]
    lines.append(f"- Source fingerprint: `{manifest['source_sha256']}`")
    lines.append(f"- Date range: {date_stamp}")
    lines.append(f"- Price date: {price_stamp}")
    lines.append(f"- Client region: {model['region_value']}")
    lines.append(f"- Scenario: {model['scenario']}")
    lines.append(f"- Image variant: {manifest.get('variant', 'default')}")
    lines.append(f"- {model['region_note']}")
    lines.append("")
    if manifest["pilot"] or not model["complete"]:
        lines.append(
            "Pilot or incomplete experiment. Do not use these samples for a public comparison."
        )
        lines.append("")
    lines.append(
        "| Provider | Work | Phase | Attempts | Verified | Wall p50 ms (95%, failures=timeout) | "
        "Guest compute p50 ms (95%) | Wall p90 ms (indicative) | Wall p10–p90 | "
        "Per-day wall p50 | Billable USD per verified result |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    for row in model["summary_rows"]:
        cost = (
            "—"
            if row["estimated_usd_per_verified"] is None
            else f"{row['estimated_usd_per_verified']:.6f}"
        )
        if row["phase"] != "cold":
            cost = "—"
        lines.append(
            f"| {row['provider']} | {row['workload']} | {row['phase']} | {row['attempts']} | "
            f"{row['verified']}/{row['attempts']} | {format_median(row['wall_stats'])} | "
            f"{format_median(row['compute_stats'])} | {format_ms(row['wall_p90_ms'])} | "
            f"{format_spread(row['wall_p10_ms'], row['wall_p90_spread_ms'])} | "
            f"{format_day_medians(row['per_day_wall_p50_ms'])} | {cost} |"
        )

    lines.append("")
    lines.append(
        f"Failed or skipped wall-time samples count as {model['deadline_ms']:.0f} ms "
        f"({model['deadline']}s deadline). Guest compute uses verified guest timers only."
    )
    lines.append(
        "Cold wall time is launch call + time-to-ready + workload. Some providers return from "
        "launch before the box accepts commands; readiness is measured with a ping probe so that "
        "time is not silently folded into the workload."
    )
    lines.append(
        f"Warm phases: after a successful cold command the runner issues {EXEC_PROBE_RUNS} "
        f"back-to-back no-op pings, then {WARM_RUNS} more workload commands on the same box. "
        f"`warm-first` is the first workload warm command (not steady state); `warm-median` is "
        f"the median across the {WARM_RUNS} warm workload commands."
    )
    lines.append(
        "Wall p90 is indicative only: with 30 samples it rests on about three observations. "
        "Prefer p50 with its interval, p10–p90 spread, and per-day medians."
    )
    lines.append("Medians show a day-blocked bootstrap 95% interval.")
    lines.append(
        "Guest compute comes from the guest timer when the workload reports it; token "
        "workloads show a dash. The one/ten JSON workloads are single-threaded; use cores, "
        "disk, net, and install to exercise additional resources."
    )
    lines.append(
        "Primary cost uses billable seconds through the last measured command (launch, ready, "
        "workload, exec probes, warm runs) and excludes harness metadata calls and cleanup wait. "
        "Full-lifecycle cost is reported separately for comparison. Both use configured usage rates "
        "and exclude plan fees and image preparation. A dash means the cost is unknown."
    )
    lines.append("")

    prose = []
    for item in model["breakdowns"]:
        text = breakdown_line(item)
        prose.append(text)
        lines.append(text)

    if model["cost_rows"]:
        lines.append("")
        lines.append("## Cost: billable vs full lifecycle")
        lines.append(
            "Billable time stops after the last measured command. Full lifecycle includes "
            "machine/placement metadata and waiting for deletion."
        )
        lines.append("")
        lines.append(
            "| Provider | Work | Billable USD/result | Full-lifecycle USD/result | "
            "Billable s p50 | Overhead s p50 |"
        )
        lines.append("|---|---|---:|---:|---:|---:|")
        for row in model["cost_rows"]:
            billable, full = (
                row["estimated_usd_per_verified"],
                row["estimated_usd_full_lifecycle_per_verified"],
            )
            billable_p50, overhead_p50 = row["billable_seconds_p50"], row["overhead_seconds_p50"]
            lines.append(
                f"| {row['provider']} | {row['workload']} | "
                f"{'—' if billable is None else f'{billable:.6f}'} | "
                f"{'—' if full is None else f'{full:.6f}'} | "
                f"{'—' if billable_p50 is None else f'{billable_p50:.2f}'} | "
                f"{'—' if overhead_p50 is None else f'{overhead_p50:.2f}'} |"
            )

    if model["exec_overhead"]:
        lines.append("")
        lines.append("## Command send overhead")
        lines.append(
            "Back-to-back no-op pings on a running box after the cold command. "
            "Tesser and Namespace start a CLI process per command (Tesser also opens SSH); "
            "Modal and E2B reuse an SDK connection. Failures count as the deadline."
        )
        lines.append("")
        lines.append(
            "| Provider | Probe attempts | Verified | p50 ms (95%) | p10–p90 ms | min ms |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|")
        for entry in model["exec_overhead"]:
            lines.append(
                f"| {entry['provider']} | {entry['attempts']} | {entry['verified']}/{entry['attempts']} | "
                f"{format_median(entry['stats'])} | "
                f"{format_spread(entry['wall_p10_ms'], entry['wall_p90_ms'])} | "
                f"{format_ms(entry['wall_min_ms'])} |"
            )
            prose.append(exec_overhead_line(entry))

    if model["burst_rows"]:
        lines.append("")
        lines.append("## Concurrent launch burst")
        for row in model["burst_rows"]:
            text = burst_line(row)
            prose.append(text)
            lines.append(text)

    if model["resume_rows"]:
        lines.append("")
        lines.append("## Snapshot / resume")
        lines.append("A cycle counts as verified when the token command succeeds after wake.")
        for row in model["resume_rows"]:
            text = resume_line(row)
            prose.append(text)
            lines.append(text)

    for entry in model["ratios"]:
        text = ratio_line(entry, model["deadline"])
        prose.append(text)
        lines.append(text)
    for skip in model["ratio_skips"]:
        text = ratio_skip_line(skip)
        prose.append(text)
        lines.append(text)

    plot_paths = [spec["relative"] for spec in model["plot_specs"]]
    if plot_paths:
        lines.append("")
        lines.append("Distribution plots:")
        for relative in plot_paths:
            lines.append(f"- {relative}")

    return "\n".join(lines) + "\n", prose


def write_artifacts(model):
    path = model["path"]
    plots_dir = path / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    plot_paths = []
    for spec in model["plot_specs"]:
        write_distribution_svg(
            path / spec["relative"], spec["series"], spec["title"], spec["xlabel"]
        )
        plot_paths.append(spec["relative"])

    text, prose = render_markdown(model)
    print(text, end="")
    (path / "report.md").write_text(text)

    summary_rows = []
    for row in model["summary_rows"]:
        summary_rows.append(
            {
                key: value
                for key, value in row.items()
                if key not in ("wall_stats", "compute_stats", "wall_values", "compute_values")
            }
        )

    breakdowns = []
    for item in model["breakdowns"]:
        entry = {
            "provider": item["provider"],
            "workload": PRIMARY_WORKLOAD,
            "phase": "cold",
            "launch_p50_ms": None if item["launch"] is None else item["launch"][0],
            "launch_p50_low_ms": None if item["launch"] is None else item["launch"][1],
            "launch_p50_high_ms": None if item["launch"] is None else item["launch"][2],
            "ready_p50_ms": None if item["ready"] is None else item["ready"][0],
            "ready_p50_low_ms": None if item["ready"] is None else item["ready"][1],
            "ready_p50_high_ms": None if item["ready"] is None else item["ready"][2],
            "workload_p50_ms": None if item["workload"] is None else item["workload"][0],
            "workload_p50_low_ms": None if item["workload"] is None else item["workload"][1],
            "workload_p50_high_ms": None if item["workload"] is None else item["workload"][2],
        }
        if item["read"]:
            entry.update(
                guest_read_p50_ms=item["read"][0],
                guest_read_p50_low_ms=item["read"][1],
                guest_read_p50_high_ms=item["read"][2],
            )
        if item["compute"]:
            entry.update(
                guest_compute_p50_ms=item["compute"][0],
                guest_compute_p50_low_ms=item["compute"][1],
                guest_compute_p50_high_ms=item["compute"][2],
            )
        breakdowns.append(entry)

    exec_overhead = [
        {key: value for key, value in entry.items() if key != "stats"}
        for entry in model["exec_overhead"]
    ]

    payload = {
        "source_sha256": model["manifest"]["source_sha256"],
        "date_range": {"start": model["start_day"], "end": model["end_day"]},
        "price_dates": model["prices"],
        "client_region": model["manifest"].get("client_region"),
        "client_region_limitation": model["region_note"],
        "scenario": model["scenario"],
        "variant": model["manifest"].get("variant", "default"),
        "workloads": list(model["workloads"]),
        "pilot": model["manifest"]["pilot"],
        "complete": model["complete"],
        "rows": summary_rows,
        "breakdowns": breakdowns,
        "exec_overhead": exec_overhead,
        "costs": model["cost_rows"],
        "ratios": model["ratios"],
        "ratio_skips": model["ratio_skips"],
        "burst": model["burst_rows"],
        "resume": model["resume_rows"],
        "notes": prose,
        "plots": plot_paths,
    }
    (path / "report.json").write_text(json.dumps(payload, indent=2) + "\n")

    fieldnames = [
        "provider",
        "workload",
        "phase",
        "attempts",
        "verified",
        "wall_p50_ms",
        "wall_p50_low_ms",
        "wall_p50_high_ms",
        "guest_compute_p50_ms",
        "guest_compute_p50_low_ms",
        "guest_compute_p50_high_ms",
        "wall_p90_ms",
        "wall_p10_ms",
        "wall_p90_spread_ms",
        "per_day_wall_p50_ms",
        "estimated_usd_per_verified",
        "estimated_usd_full_lifecycle_per_verified",
    ]
    with (path / "report.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary_rows:
            csv_row = {key: row.get(key) for key in fieldnames}
            days = csv_row.get("per_day_wall_p50_ms") or {}
            csv_row["per_day_wall_p50_ms"] = ";".join(
                f"{day}:{value}" for day, value in days.items()
            )
            writer.writerow(csv_row)
