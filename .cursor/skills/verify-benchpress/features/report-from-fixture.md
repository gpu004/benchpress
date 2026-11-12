# Report from fixture

Report from fixture turns sanitized pilot attempt rows into the same user-facing report artifacts a live experiment produces: markdown, JSON, CSV, and SVG strip plots.

## Sub-features

- `report-cli` runs `benchpress.py report <dir>` on a directory with `experiment.json` and `attempts.jsonl`.
- `report-markdown` writes `report.md` with the pilot banner and provider/workload/phase table.
- `report-json-csv` writes `report.json` and `report.csv` beside the markdown.
- `report-plots` writes SVG files under `plots/` and lists them in `report.md`.

## How to get to it (user POV)

- Run `uv run --locked python benchpress.py report <experiment-dir>` after a `run` finished, or against copied fixture rows.
- Open `report.md` in the experiment directory.
- Open listed paths under `plots/` for distribution SVGs.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- `$BENCHPRESS_VERIFY_SCRATCH` and `$BENCHPRESS_VERIFY_EVIDENCE` are set from `launch.sh`.
- `fixtures/pilot/attempts.jsonl` and `fixtures/pilot/experiment.json` exist.

- **Copy fixture into scratch.** The helper copies both fixture files into `$BENCHPRESS_VERIFY_SCRATCH/report-fixture/`. Run `.cursor/skills/verify-benchpress/helpers/drive-report.sh`. The scratch experiment directory contains only those two inputs before the command.
- **Generate report.** The helper runs `uv run --locked python benchpress.py report "$BENCHPRESS_VERIFY_SCRATCH/report-fixture"`. Exit code is `0` and stdout begins with `# Benchpress results`.
- **Confirm pilot banner.** Read `$BENCHPRESS_VERIFY_EVIDENCE/report-from-fixture/report.md`. It contains `Pilot or incomplete experiment. Do not use these samples for a public comparison.`
- **Confirm table row.** The same `report.md` contains `| tesser | one | cold |` and wall p50 `5813.44`.
- **Confirm plots.** `report.md` lists `plots/one-cold-wall.svg` and that SVG exists under the evidence `plots/` copy.
- **Confirm machine-readable side effects.** Evidence copies of `report.json` and `report.csv` exist; `report.json` has `pilot: true`, `scenario: "standard"`, and a `source_sha256` starting with `e42b9c4c`.
- **Proof.** Keep the evidence directory `$BENCHPRESS_VERIFY_EVIDENCE/report-from-fixture/` (stdout, stderr, exit code, report files, plots). Do not delete it during cleanup.

## Gotchas

- `fixtures/pilot/experiment.json` is schema 1 (legacy). Report still works via the legacy workload default; do not “fix” the fixture mid-proof unless the change under test is schema migration.
- Reporting into a live `results/` directory mutates user data. Always use the scratch copy.
- Wall numbers are fixture-specific. If the fixture changes, update the expected `5813.44` assertion in `drive-report.sh` and this file together.
- A green unit suite alone does not prove this path. Drive the CLI.
