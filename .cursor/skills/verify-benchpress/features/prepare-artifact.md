# Prepare an artifact

Prepare builds the provider image, snapshot, or template that timed runs consume, printing a JSON object with the artifact id and `prepare_seconds`.

## Sub-features

- `prepare-default` builds the default payload variant.
- `prepare-large` builds `--variant large` with a 256 MiB filler.
- `prepare-record` shows the id a user copies into `benchpress.local.toml`.

## How to get to it (user POV)

- `uv run --locked python benchpress.py prepare PROVIDER`
- `uv run --locked python benchpress.py prepare PROVIDER --variant large`
- Copy returned `snapshot` / `image` / `template` into local config overrides.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- Provider auth available for the chosen provider.
- For Tesser: commit state is clean enough for `git rev-parse HEAD` during prepare paths that need it; follow README commit guidance before Tesser snapshot create.

- **Run prepare.** `uv run --locked python benchpress.py prepare modal` (or another provider). Capture stdout JSON under `$BENCHPRESS_VERIFY_EVIDENCE/prepare-<provider>/stdout.json`.
- **Confirm artifact field.** JSON includes `provider`, a non-empty artifact key (`image` / `snapshot` / `template`), `variant`, and numeric `prepare_seconds`.
- **Confirm exit.** Process exit code `0`.
- **Proof.** Keep the JSON transcript. Do not delete the remote artifact as part of verification cleanup unless the user asked; preparation charges are separate.

## Gotchas

- Prepare writes `.benchpress-variant` only inside a temp payload copy (or a private Tesser clone), never the live `payload/` tree. Parallel prepares are still heavy on provider quotas; prefer one at a time.
- `payload/prepare.sh` must succeed inside the build; a broken npm copy aborts preparation.
- Missing auth fails prepare. Report unreachable rather than simulating an id.
- Writing the returned id into `benchpress.local.toml` is a user config step; verification may record the id in evidence without editing the user's local file unless asked.
