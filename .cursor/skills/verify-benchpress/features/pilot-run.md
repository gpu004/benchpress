# Pilot run

A pilot run executes a short timed experiment into an `--out` directory, writing `experiment.json` and `attempts.jsonl` the report command can consume. Standard and resume pilots interleave scheduled attempts; burst pilots launch concurrent boxes instead.

## Sub-features

- `pilot-standard` `--pilot` standard cold/warm workloads.
- `pilot-burst` `--pilot --mode burst --concurrency N`.
- `pilot-resume` `--pilot --mode resume`.
- `pilot-narrow` `--providers` / `--workloads` filters for a cheaper proof.

## How to get to it (user POV)

- `uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --out results/pilot`
- Burst / resume variants from the README.
- Then `uv run --locked python benchpress.py report <out-dir>`.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- `benchpress.local.toml` contains prepared artifact ids for every provider you pass.
- Provider auth works for those providers.
- Choose an isolated out dir under `$BENCHPRESS_VERIFY_SCRATCH`, never a shared `results/` folder.

- **Narrow pilot.** Example: `uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --providers modal --workloads token --out "$BENCHPRESS_VERIFY_SCRATCH/pilot-modal-token"`.
- **Confirm outputs.** The out dir contains `experiment.json` and at least one line in `attempts.jsonl`.
- **Confirm attempt shape.** A JSONL row has `provider`, `workload`, and `cold.status`. Successful closes also include `cleanup`; some connect-failure rows omit it.
- **Report the out dir.** `uv run --locked python benchpress.py report "$BENCHPRESS_VERIFY_SCRATCH/pilot-modal-token"` writes `report.md` with the pilot banner.
- **Proof.** Copy `experiment.json`, `attempts.jsonl`, `report.md`, and command transcripts into `$BENCHPRESS_VERIFY_EVIDENCE/pilot-run/`.

## Gotchas

- Without prepared artifact ids, launch fails with setup errors. That is not a report bug.
- Burst requires `--concurrency N` with `N >= 2` and rejects `--workloads`.
- Formal (non-pilot) series need `--client-region us-east` and `--start-date`; do not use that path for a cheap verification unless explicitly requested.
- Parallel Tesser burst cleanup can be unsafe if a worker lacks a returned box id; prefer a single-provider narrow pilot for proof unless testing burst itself.
- Always clean up provider boxes the run created; retain only local evidence files.
