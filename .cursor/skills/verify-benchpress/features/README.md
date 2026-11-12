# Benchpress verification map

This directory is the maintained source for verifying user-facing Benchpress CLI behavior. Read the index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- Work from the repo root with `uv` 0.13.7 and Python 3.15.2 via uv.
- Run `eval "$(.cursor/skills/verify-benchpress/helpers/launch.sh)"` so scratch and evidence dirs are isolated.
- Run `.cursor/skills/verify-benchpress/helpers/doctor.sh` and require `doctor: READY`.
- Never drive a shared `results/` directory already used by a human run.
- Cloud features additionally need provider CLIs/SDKs signed in and a local `benchpress.local.toml` with prepared artifact IDs.

## Driving conventions

- Start every recipe from the launched baseline unless its preconditions say otherwise.
- Prefer the helpers under `.cursor/skills/verify-benchpress/helpers/`.
- Treat every command as literal. Keep quoted names and flags unchanged.
- Offline features must not call cloud providers.
- Restore nothing destructive to the user's live provider accounts during cleanup beyond boxes the verification run itself created.
- Retain proof artifacts under `$BENCHPRESS_VERIFY_EVIDENCE`.

## Proof and skip reporting

- Capture the CLI action and the resulting artifacts, not only the exit code.
- CLI proof includes the command, stdout, stderr, and exit code.
- Mutation/report proof includes the written files (`report.md`, JSON/CSV/SVG, or smoke JSON).
- Record the feature ID with every artifact directory name.
- Report an unreachable path with the unmet precondition (missing auth, missing artifact ID).
- Do not report a skipped cloud entry point as verified through the offline report path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features`
2. `How to get to it (user POV)`
3. `Driving it with verify-benchpress`
4. `Gotchas`

## Features

- [Report from fixture](./report-from-fixture.md) — offline report generation from sanitized pilot rows.
- [Unit and property tests](./unit-and-property-tests.md) — local unittest + hegel suite.
- [Resolve cleanup](./resolve-cleanup.md) — operator receipt for a saved cleanup warning.
- [Reconcile allocation](./reconcile-allocation.md) — operator receipt for a blocked allocation journal entry.
- [Smoke a provider](./smoke-provider.md) — disposable (or existing Namespace) smoke box.
- [Prepare an artifact](./prepare-artifact.md) — build provider image/snapshot/template.
- [Pilot run](./pilot-run.md) — short timed pilot into an isolated `--out` directory.
