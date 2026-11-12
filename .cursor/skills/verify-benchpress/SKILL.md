---
name: verify-benchpress
description: Drive the Benchpress CLI the way a user does — report generation from fixture rows, local tests, and (when credentials exist) smoke/prepare/pilot. Use when proving Benchpress behavior, validating report output, or checking a change did not break the harness CLI.
---

# Verify Benchpress

Benchpress is a short-lived Python CLI (`benchpress.py`) that prepares cloud sandbox images, runs timed workloads, and writes reports. There is no web UI and no long-running local server. Primary offline verification is `report` against [`fixtures/pilot/`](../../../fixtures/pilot/). Cloud `smoke` / `prepare` / `run` need provider auth and are mapped separately.

## Launch

From the repo root:

```sh
eval "$(.cursor/skills/verify-benchpress/helpers/launch.sh)"
.cursor/skills/verify-benchpress/helpers/doctor.sh
```

`launch.sh`:

- runs `uv sync --locked --python 3.15.2 --group dev`
- optionally `npm ci` when `node_modules` is missing
- creates `$BENCHPRESS_VERIFY_SCRATCH` under `/tmp/benchpress-verify-<RUN_ID>/`
- creates `$BENCHPRESS_VERIFY_EVIDENCE` under `.cursor/skills/verify-benchpress/artifacts/<RUN_ID>/`
- prints shell exports on stdout (safe to `eval`)

Ready means: doctor prints `doctor: READY`, modules import, and `fixtures/pilot/{attempts.jsonl,experiment.json}` exist. There is no process to keep alive after launch.

Teardown:

```sh
.cursor/skills/verify-benchpress/helpers/cleanup.sh
```

Cleanup deletes only `/tmp/benchpress-verify-*` scratch. Evidence under `artifacts/<RUN_ID>/` must remain.

## Doctor

```sh
.cursor/skills/verify-benchpress/helpers/doctor.sh
```

Read-only checks: uv `0.13.7`, Python `3.15.2` via uv, imports, pilot fixtures, CLI help listing `{smoke,prepare,run,report,resolve-cleanup,reconcile}`. Run doctor first whenever anything looks off. Never drive a shared live results directory (`results/`) for verification; always use the scratch dir from launch.

## Drive

Prefer helpers. Treat every command as literal.

Offline (default proof path):

```sh
.cursor/skills/verify-benchpress/helpers/drive-report.sh
.cursor/skills/verify-benchpress/helpers/drive-tests.sh
```

Manual equivalents (same observables):

```sh
mkdir -p "$BENCHPRESS_VERIFY_SCRATCH/report-fixture"
cp fixtures/pilot/attempts.jsonl fixtures/pilot/experiment.json \
  "$BENCHPRESS_VERIFY_SCRATCH/report-fixture/"
uv run --locked python benchpress.py report \
  "$BENCHPRESS_VERIFY_SCRATCH/report-fixture"
uv run --locked --group dev python -m unittest test_benchpress test_providers test_allocation test_allocation_model -v
uv run --locked --group dev python test_properties.py
```

Cloud paths (only when auth + artifacts are available; see feature map Gotchas):

```sh
uv run --locked python benchpress.py smoke modal
uv run --locked python benchpress.py --config benchpress.local.toml prepare modal
uv run --locked python benchpress.py --config benchpress.local.toml run --pilot \
  --providers modal --workloads token \
  --out "$BENCHPRESS_VERIFY_SCRATCH/pilot-modal"
```

Never write verification runs into a shared `results/` directory already in use by the user.

## Evidence

Proof lives in `$BENCHPRESS_VERIFY_EVIDENCE` (default `.cursor/skills/verify-benchpress/artifacts/<RUN_ID>/`).

Standards:

- Exercise the real CLI entry points (`benchpress.py report|smoke|prepare|run`), not internal Python setters.
- Capture the command outcome and resulting artifacts (stdout/stderr/exit code plus written files).
- For report: require `report.md`, `report.json`, `report.csv`, and at least one SVG under `plots/`.
- For report content: require the pilot banner, a `tesser | one | cold` table row, wall p50 `5813.44` from the fixture, and `plots/one-cold-wall.svg`.
- Mocks are only acceptable inside the unit/property suite; do not mock providers when driving smoke/prepare/run.
- Credential-gated features that cannot run must be reported as failed/unreachable with the unmet precondition — do not mark them verified via a different path.

## Cleanup

```sh
.cursor/skills/verify-benchpress/helpers/cleanup.sh
```

Removes `$BENCHPRESS_VERIFY_SCRATCH` only when it matches `/tmp/benchpress-verify-*`. Does not kill processes by name. Does not delete `$BENCHPRESS_VERIFY_EVIDENCE`.

## Helpers

All helpers are executable under `.cursor/skills/verify-benchpress/helpers/`:

| Helper | Purpose |
|---|---|
| `launch.sh` | Sync deps; allocate scratch + evidence; emit exports |
| `doctor.sh` | Read-only readiness check |
| `drive-report.sh` | Report against `fixtures/pilot/`; copy proof |
| `drive-tests.sh` | Unit + property tests; copy transcripts |
| `cleanup.sh` | Delete scratch; retain evidence |

## Feature map

See [`features/README.md`](features/README.md). Prove at least one mapped feature per verification run; start with `report-from-fixture` when offline.
