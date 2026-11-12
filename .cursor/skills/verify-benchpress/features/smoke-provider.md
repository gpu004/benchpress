# Smoke a provider

Smoke launches (or reuses) one box, runs a minimal command path, and records cleanup. Success means cold status `ok` and cleanup `removed` (or `existing_box_kept` for Namespace `--existing`).

## Sub-features

- `smoke-tesser` disposable Tesser box via `tesser exec` checkout sync check.
- `smoke-modal` disposable Modal sandbox.
- `smoke-e2b` disposable E2B sandbox.
- `smoke-namespace-existing` Namespace against `--existing YOUR_DEVBOX_NAME` (box retained).
- `smoke-namespace-fresh` Namespace fresh launch when the account has capacity.

## How to get to it (user POV)

- `uv run --locked python benchpress.py smoke tesser`
- `uv run --locked python benchpress.py smoke modal`
- `uv run --locked python benchpress.py smoke e2b`
- `uv run --locked python benchpress.py smoke namespace --existing YOUR_DEVBOX_NAME`
- `uv run --locked python benchpress.py smoke namespace`

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- Provider CLI/SDK signed in for the chosen provider (Tesser CLI, Modal, E2B key/login, Namespace `devbox`).
- For Namespace existing: a live box name the account owns.

- **Pick one provider.** Prefer `modal` or `e2b` when those credentials are present.
- **Run smoke.** `uv run --locked python benchpress.py smoke modal` (replace provider). Capture stdout JSON to `$BENCHPRESS_VERIFY_EVIDENCE/smoke-<provider>/stdout.json`.
- **Confirm cold ok.** Parsed JSON has `cold.status == "ok"`.
- **Confirm cleanup.** Parsed JSON has `cleanup` in `removed` or `existing_box_kept`. Process exit code is `0`.
- **Proof.** Save stdout, stderr, and exit code under the evidence directory named for the provider.

## Gotchas

- Without provider auth this feature is unreachable. Report the missing login/key; do not mark verified via report fixtures.
- Namespace accounts with a one-box limit reject fresh launches; use `--existing` or skip with that reason.
- Smoke creates real billable resources. Cleanup must succeed; if cleanup fails, stop and inspect before more runs.
- Do not pass `--existing` for non-Namespace providers.
