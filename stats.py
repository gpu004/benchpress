"""Bootstrap intervals, censoring, and ratio gates for reports."""

import math
import random
import statistics
from collections import defaultdict

from constants import DEADLINE_MS, RATIO_MIN_VERIFIED, WARM_RUNS


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def bootstrap_median(values, blocks=None, *, draws=5000, seed=7256):
    if not values:
        return None
    mid = statistics.median(values)
    if len(values) == 1:
        return mid, mid, mid
    rng = random.Random(seed)
    samples = []
    if blocks is None:
        for _ in range(draws):
            samples.append(statistics.median(rng.choices(values, k=len(values))))
    else:
        by_day = defaultdict(list)
        for block, value in zip(blocks, values):
            by_day[block // 10].append(value)
        for _ in range(draws):
            sample = [
                value for group in by_day.values() for value in rng.choices(group, k=len(group))
            ]
            samples.append(statistics.median(sample))
    return mid, percentile(samples, 0.025), percentile(samples, 0.975)


def interval(pairs):
    if not pairs:
        return None, None
    rng = random.Random(7256)
    by_day = defaultdict(list)
    for block, pair in pairs.items():
        by_day[block // 10].append(pair)
    draws = []
    for _ in range(5000):
        sample = [pair for group in by_day.values() for pair in rng.choices(group, k=len(group))]
        left, right = zip(*sample)
        right_mid = statistics.median(right)
        if right_mid == 0:
            continue
        draws.append(statistics.median(left) / right_mid)
    if not draws:
        return None, None
    return percentile(draws, 0.025), percentile(draws, 0.975)


def warm_runs(sample):
    if not sample or sample.get("status") == "not_run":
        return []
    if "runs" in sample:
        return sample["runs"]
    return [sample]


def verified_command_count(group):
    total = 0
    for row in group:
        if row["cold"].get("status") == "ok":
            total += 1
        total += sum(run["status"] == "ok" for run in warm_runs(row.get("warm")))
    return total


def verified_metric(group, phase, key):
    values, blocks = [], []
    for row in group:
        if phase == "warm-first":
            runs = warm_runs(row.get("warm"))
            sample = runs[0] if runs else None
        elif phase == "warm-median":
            sample = row.get("warm")
        else:
            sample = row.get(phase)
        if not sample or sample.get("status") != "ok":
            continue
        if key == "wall_ms":
            if phase == "warm-median":
                value = sample.get("wall_median_ms", sample.get("wall_ms"))
            elif phase == "warm-first":
                value = sample.get("wall_ms")
            else:
                value = sample.get("wall_ms")
            if value is None:
                continue
            values.append(value)
            blocks.append(row["block"])
            continue
        guest = sample.get("guest") or {}
        if key in guest:
            values.append(guest[key])
            blocks.append(row["block"])
    return values, blocks


def censored_wall(group, phase, *, deadline_ms=DEADLINE_MS):
    """Count failed attempts at the experiment deadline."""
    values, blocks = [], []
    for row in group:
        if phase == "cold":
            sample = row.get("cold") or {}
            if sample.get("status") == "ok":
                values.append(sample["wall_ms"])
            else:
                values.append(deadline_ms)
            blocks.append(row["block"])
            continue
        warm = row.get("warm") or {}
        runs = warm_runs(warm)
        if phase == "warm-first":
            if runs and runs[0].get("status") == "ok":
                values.append(runs[0]["wall_ms"])
            else:
                values.append(deadline_ms)
            blocks.append(row["block"])
            continue
        if phase == "warm-median":
            warm = row.get("warm") or {}
            if warm.get("runs"):
                walls = [
                    run["wall_ms"] if run["status"] == "ok" else deadline_ms for run in warm["runs"]
                ]
                while len(walls) < WARM_RUNS:
                    walls.append(deadline_ms)
                values.append(statistics.median(walls[:WARM_RUNS]))
            elif warm.get("status") == "ok":
                values.append(warm.get("wall_median_ms", warm["wall_ms"]))
            else:
                values.append(deadline_ms)
            blocks.append(row["block"])
    return values, blocks


def spread_ms(values):
    if not values:
        return None, None
    return percentile(values, 0.10), percentile(values, 0.90)


def per_day_medians(values, blocks):
    by_day = defaultdict(list)
    for block, value in zip(blocks, values):
        by_day[block // 10].append(value)
    return {day: statistics.median(group) for day, group in sorted(by_day.items())}


def ratio_ready(verified_left, verified_right, paired):
    return (
        verified_left >= RATIO_MIN_VERIFIED
        and verified_right >= RATIO_MIN_VERIFIED
        and paired >= RATIO_MIN_VERIFIED
    )
