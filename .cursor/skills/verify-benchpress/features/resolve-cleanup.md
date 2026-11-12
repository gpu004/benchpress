# Resolve cleanup

Resolve cleanup records operator attestation that a saved cleanup warning (`cleanup="failed"` or `cleanup="creation_unconfirmed"`) reached a confirmed terminal outcome, unblocking further launches in that experiment directory. It does not call providers or delete boxes.

## Sub-features

- `resolve-removed` records `--confirmed-outcome removed` with nonempty operator, scope, and evidence.
- `resolve-absent` records `--confirmed-outcome absent` only when absence is authoritative.
- `resolve-receipt` writes `cleanup-resolutions/<attempt_id>.json` without mutating `attempts.jsonl`.

## How to get to it (user POV)

- Hit a blocked restart whose error lists a full attempt ID and allocation.
- Run `uv run --locked python benchpress.py resolve-cleanup --out <dir> --attempt <id> --resolved-by … --allocation-scope … --evidence … --confirmed-outcome removed|absent`.
- Confirm a receipt appears under `<dir>/cleanup-resolutions/`.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- `$BENCHPRESS_VERIFY_SCRATCH` and `$BENCHPRESS_VERIFY_EVIDENCE` are set from `launch.sh`.
- Offline: seed a scratch experiment with one cleanup-warning row (helper does this).

- **Seed and resolve.** Run `.cursor/skills/verify-benchpress/helpers/drive-resolve-cleanup.sh`.
- **Confirm receipt.** Evidence copy of the receipt JSON has `schema: 1`, matching `attempt_id`, `confirmed_outcome: "removed"`, and nonempty `resolved_by` / `allocation_scope` / `evidence` / `resolved_at`.
- **Confirm attempts unchanged.** The helper asserts `attempts.jsonl` bytes are identical before and after the CLI call.
- **Proof.** Keep `$BENCHPRESS_VERIFY_EVIDENCE/resolve-cleanup/` (stdout, stderr, exit code, receipt, attempts copy).

## Gotchas

- Without a matching cleanup-warning row, the CLI rejects the attempt ID. Do not forge a receipt by copying another attempt's JSON.
- Empty operator, scope, or evidence strings fail validation.
- `--confirmed-outcome absent` is for authoritative absence only; lost lookups and empty lists are not absence.
- This path is offline-verifiable with seeded scratch state. Live blocked experiments still require real provider ownership checks before calling the CLI.
