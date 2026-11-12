"""Property-based tests for pure scheduling, stats, and verification helpers."""

import json
import math
import os
import statistics

import hegel
from hegel import generators as gs

from lifecycle import (
    CHECKSUMS,
    DEADLINE_MS,
    NODE,
    error_text,
    parse_marker,
    summarize_timed_runs,
    verify,
)
from scenarios import schedule
from stats import (
    RATIO_MIN_VERIFIED,
    bootstrap_median,
    censored_wall,
    interval,
    per_day_medians,
    percentile,
    ratio_ready,
    spread_ms,
    verified_command_count,
    warm_runs,
)

finite_ms = gs.floats(min_value=0.0, max_value=200_000.0, allow_nan=False, allow_infinity=False)
finite_numbers = gs.floats(
    min_value=-1_000.0, max_value=1_000.0, allow_nan=False, allow_infinity=False
)
fractions = gs.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
key_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


@hegel.test(test_cases=80)
def test_schedule_covers_each_pair_once_per_block(tc):
    n_providers = tc.draw(gs.integers(1, 4))
    n_workloads = tc.draw(gs.integers(1, 5))
    blocks = tc.draw(gs.integers(1, 6))
    seed = tc.draw(gs.integers(0, 10_000))
    providers = tuple(f"p{i}" for i in range(n_providers))
    workloads = tuple(f"w{i}" for i in range(n_workloads))

    tasks = list(schedule(providers, seed, blocks=blocks, workloads=workloads))
    assert len(tasks) == blocks * n_providers * n_workloads
    assert len(set(tasks)) == len(tasks)
    assert list(schedule(providers, seed, blocks=blocks, workloads=workloads)) == tasks

    expected = {(workload, provider) for workload in workloads for provider in providers}
    for block in range(blocks):
        pairs = {(workload, provider) for index, workload, provider in tasks if index == block}
        assert pairs == expected


@hegel.test(test_cases=100)
def test_ratio_ready_matches_threshold(tc):
    left = tc.draw(gs.integers(0, 40))
    right = tc.draw(gs.integers(0, 40))
    paired = tc.draw(gs.integers(0, 40))
    expected = (
        left >= RATIO_MIN_VERIFIED and right >= RATIO_MIN_VERIFIED and paired >= RATIO_MIN_VERIFIED
    )
    assert ratio_ready(left, right, paired) is expected


@hegel.test(test_cases=100)
def test_percentile_is_order_statistic(tc):
    values = tc.draw(gs.lists(finite_numbers, min_size=1, max_size=40))
    fraction = tc.draw(fractions)
    result = percentile(values, fraction)
    assert result in values
    assert min(values) <= result <= max(values)

    low = tc.draw(fractions)
    high = tc.draw(fractions)
    left, right = sorted((low, high))
    assert percentile(values, left) <= percentile(values, right)


@hegel.test(test_cases=60)
def test_bootstrap_median_center_and_seed(tc):
    values = tc.draw(gs.lists(finite_numbers, min_size=1, max_size=25))
    seed = tc.draw(gs.integers(0, 10_000))
    draws = tc.draw(gs.integers(20, 80))
    result = bootstrap_median(values, draws=draws, seed=seed)
    assert result is not None
    mid, low, high = result
    assert mid == statistics.median(values)
    assert bootstrap_median(values, draws=draws, seed=seed) == result
    if len(values) == 1:
        assert result == (values[0], values[0], values[0])
    else:
        assert isinstance(low, (float, int))
        assert isinstance(high, (float, int))


@hegel.test(test_cases=40)
def test_bootstrap_median_empty_is_none(tc):
    assert bootstrap_median([]) is None


@hegel.test(test_cases=80)
def test_spread_and_per_day_medians(tc):
    assert spread_ms([]) == (None, None)
    values = tc.draw(gs.lists(finite_numbers, min_size=1, max_size=30))
    low, high = spread_ms(values)
    assert low in values and high in values
    assert low <= high

    blocks = tc.draw(gs.lists(gs.integers(0, 29), min_size=len(values), max_size=len(values)))
    day_medians = per_day_medians(values, blocks)
    assert set(day_medians) == {block // 10 for block in blocks}
    for day, median in day_medians.items():
        group = [value for block, value in zip(blocks, values) if block // 10 == day]
        assert median == statistics.median(group)


@hegel.test(test_cases=120)
def test_error_text_redacts_e2b_tokens(tc):
    suffix = tc.draw(gs.text(alphabet=key_chars, min_size=8, max_size=40))
    prefix = tc.draw(gs.sampled_from(("e2b_", "sk_e2b_")))
    token = prefix + suffix
    before = tc.draw(gs.text(alphabet="abcxz012=_-!", max_size=40))
    after = tc.draw(gs.text(alphabet="abcxz012=_-!,.", max_size=40))
    message = f"{before}{token}{after}"
    rendered = error_text(RuntimeError(message))
    assert token not in rendered
    assert "<redacted>" in rendered
    assert rendered.startswith("RuntimeError:")
    assert len(rendered) <= 1800


@hegel.test(test_cases=80)
def test_summarize_timed_runs_failed_wall_is_censored(tc):
    """Failures must not look fast via summary wall_ms (same rule as wall_first_ms)."""
    runs = tc.draw(
        gs.lists(
            gs.fixed_dicts(
                {
                    "status": gs.sampled_from(("ok", "timeout", "failed")),
                    "wall_ms": finite_ms,
                }
            ),
            min_size=1,
            max_size=6,
        )
    )
    summary = summarize_timed_runs(runs)
    walls = [run["wall_ms"] if run["status"] == "ok" else DEADLINE_MS for run in runs]
    assert summary["wall_ms"] == walls[0]
    assert summary["wall_first_ms"] == walls[0]
    if runs[0]["status"] != "ok":
        assert summary["wall_ms"] == DEADLINE_MS


@hegel.test(test_cases=60)
def test_interval_handles_zero_baseline_median(tc):
    n = tc.draw(gs.integers(1, 12))
    pairs = {}
    for block in range(n):
        left = tc.draw(finite_ms)
        right = tc.draw(gs.sampled_from((0.0, 0.0, tc.draw(finite_ms))))
        pairs[block] = (left, right)
    result = interval(pairs)
    assert result is not None
    low, high = result
    if all(right == 0 for _, right in pairs.values()):
        assert low is None and high is None
    else:
        assert math.isfinite(low) and math.isfinite(high)
        assert low <= high


@hegel.test(test_cases=60)
def test_error_text_redacts_env_api_key(tc):
    key = "e2b_" + tc.draw(gs.text(alphabet=key_chars, min_size=20, max_size=32))
    previous = os.environ.get("E2B_API_KEY")
    os.environ["E2B_API_KEY"] = key
    try:
        rendered = error_text(ValueError(f"boom {key} done"))
    finally:
        if previous is None:
            del os.environ["E2B_API_KEY"]
        else:
            os.environ["E2B_API_KEY"] = previous
    assert key not in rendered
    assert "<redacted>" in rendered


@hegel.test(test_cases=80)
def test_parse_marker_round_trips_one_line(tc):
    payload = tc.draw(
        gs.dicts(
            gs.text(alphabet="abc", min_size=1, max_size=6),
            gs.one_of(gs.integers(-100, 100), gs.text(alphabet="xyz", max_size=8), gs.booleans()),
            max_size=6,
        )
    )
    marker = "BENCHPRESS_RESULT "
    prefix = tc.draw(gs.text(alphabet="nois\n", max_size=12))
    suffix = tc.draw(gs.text(alphabet="nois\n", max_size=12))
    output = f"{prefix}\n{marker}{json.dumps(payload)}\n{suffix}"
    tc.assume(sum(line.startswith(marker) for line in output.splitlines()) == 1)
    assert parse_marker(output, marker) == payload


@hegel.test(test_cases=80)
def test_summarize_timed_runs_censors_failures(tc):
    runs = tc.draw(
        gs.lists(
            gs.fixed_dicts(
                {
                    "status": gs.sampled_from(("ok", "timeout", "failed")),
                    "wall_ms": finite_ms,
                },
                optional={"error": gs.text(alphabet="err", max_size=8)},
            ),
            min_size=1,
            max_size=8,
        )
    )
    summary = summarize_timed_runs(runs)
    walls = [run["wall_ms"] if run["status"] == "ok" else DEADLINE_MS for run in runs]
    assert summary["wall_ms"] == walls[0]
    assert summary["wall_first_ms"] == walls[0]
    assert summary["wall_median_ms"] == statistics.median(walls)
    assert summary["wall_min_ms"] == min(walls)
    assert summary["wall_min_ms"] <= summary["wall_median_ms"]
    if all(run["status"] == "ok" for run in runs):
        assert summary["status"] == "ok"
    else:
        assert summary["status"] != "ok"


@hegel.test(test_cases=60)
def test_censored_wall_counts_failures_as_deadline(tc):
    phase = tc.draw(gs.sampled_from(("cold", "warm-first", "warm-median")))
    group = []
    size = tc.draw(gs.integers(1, 8))
    for index in range(size):
        cold_ok = tc.draw(gs.booleans())
        warm_ok = tc.draw(gs.booleans())
        cold_wall = tc.draw(finite_ms)
        warm_walls = tc.draw(gs.lists(finite_ms, min_size=1, max_size=3))
        row = {
            "block": index,
            "cold": {"status": "ok" if cold_ok else "timeout", "wall_ms": cold_wall},
            "warm": {
                "status": "ok" if warm_ok else "timeout",
                "wall_ms": warm_walls[0],
                "wall_median_ms": statistics.median(warm_walls),
                "runs": [
                    {"status": "ok" if warm_ok else "timeout", "wall_ms": wall}
                    for wall in warm_walls
                ],
            },
        }
        group.append(row)

    values, blocks = censored_wall(group, phase)
    assert len(values) == len(group) == len(blocks)
    assert blocks == [row["block"] for row in group]
    for value, row in zip(values, group):
        if phase == "cold":
            if row["cold"]["status"] == "ok":
                assert value == row["cold"]["wall_ms"]
            else:
                assert value == DEADLINE_MS
        elif phase == "warm-first":
            if row["warm"]["runs"][0]["status"] == "ok":
                assert value == row["warm"]["runs"][0]["wall_ms"]
            else:
                assert value == DEADLINE_MS
        else:
            assert value >= 0
            if row["warm"]["status"] != "ok" and not row["warm"]["runs"]:
                assert value == DEADLINE_MS


@hegel.test(test_cases=80)
def test_warm_runs_and_verified_counts(tc):
    assert warm_runs(None) == []
    assert warm_runs({"status": "not_run"}) == []
    assert warm_runs({"status": "ok", "runs": []}) == []

    sample = tc.draw(
        gs.fixed_dicts(
            {
                "status": gs.sampled_from(("ok", "failed")),
                "wall_ms": finite_ms,
            },
            optional={
                "runs": gs.lists(
                    gs.fixed_dicts(
                        {"status": gs.sampled_from(("ok", "failed")), "wall_ms": finite_ms}
                    ),
                    min_size=0,
                    max_size=4,
                ),
            },
        )
    )
    runs = warm_runs(sample)
    if "runs" in sample:
        assert runs == sample["runs"]
    else:
        assert runs == [sample]

    group = []
    size = tc.draw(gs.integers(0, 6))
    expected = 0
    for index in range(size):
        cold_ok = tc.draw(gs.booleans())
        warm = tc.draw(
            gs.one_of(
                gs.just({"status": "not_run"}),
                gs.fixed_dicts(
                    {
                        "status": gs.just("ok"),
                        "runs": gs.lists(
                            gs.fixed_dicts(
                                {
                                    "status": gs.sampled_from(("ok", "failed")),
                                    "wall_ms": finite_ms,
                                }
                            ),
                            min_size=1,
                            max_size=3,
                        ),
                    }
                ),
            )
        )
        if cold_ok:
            expected += 1
        expected += sum(run["status"] == "ok" for run in warm_runs(warm))
        group.append(
            {
                "block": index,
                "cold": {"status": "ok" if cold_ok else "failed"},
                "warm": warm,
            }
        )
    assert verified_command_count(group) == expected


@hegel.test(test_cases=40)
def test_verify_accepts_well_formed_token_results(tc):
    token = tc.draw(gs.text(alphabet=key_chars, min_size=1, max_size=24))
    noise = tc.draw(gs.text(alphabet="log\n", max_size=16))
    payload = {"workload": "token", "token": token, "node": NODE}
    output = f"{noise}\nBENCHPRESS_RESULT {json.dumps(payload)}\n"
    tc.assume(sum(line.startswith("BENCHPRESS_RESULT ") for line in output.splitlines()) == 1)
    assert verify(output, "token", token) == payload

    wrong = tc.draw(gs.text(alphabet=key_chars, min_size=1, max_size=24))
    tc.assume(wrong != token)
    try:
        verify(output, "token", wrong)
    except ValueError as error:
        assert "token" in str(error)
    else:
        raise AssertionError("expected token mismatch")


@hegel.test(test_cases=30)
def test_verify_accepts_checksummed_workloads(tc):
    workload = tc.draw(gs.sampled_from(("one", "ten", "cores", "disk", "net", "install")))
    token = tc.draw(gs.text(alphabet=key_chars, min_size=1, max_size=16))
    payload = {"workload": workload, "token": token, "node": NODE, "checksum": CHECKSUMS[workload]}
    if workload in ("one", "ten"):
        payload.update(
            bytes=32 * 1024**2,
            records=524288 * (10 if workload == "ten" else 1),
            read_ms=1.0,
            compute_ms=2.0,
        )
    elif workload == "cores":
        payload.update(workers=2, bytes=32 * 1024**2, records=524288 * 2, compute_ms=3.0)
    elif workload == "disk":
        payload.update(bytes=64 * 1024**2, write_ms=1.0, fsync_ms=1.0, read_ms=1.0, compute_ms=1.0)
    elif workload == "net":
        payload.update(bytes=1024, download_ms=1.0, compute_ms=1.0)
    else:
        payload.update(install_ms=1.0, compute_ms=1.0)
    output = "BENCHPRESS_RESULT " + json.dumps(payload)
    assert verify(output, workload, token) == payload


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if getattr(value, "is_hegel_test", False):
            print(name)
            value()
    print("all property tests passed")
