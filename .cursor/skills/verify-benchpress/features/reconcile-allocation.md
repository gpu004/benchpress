# Reconcile allocation

Reconcile records operator evidence that a durable allocation journal entry is terminally cleaned when automatic recovery cannot finish. It advances the allocation phase to `cleanup_confirmed` without rewriting measurement history or calling providers.

## Sub-features

- `reconcile-cli` runs `benchpress.py reconcile --out <dir> --allocation <id> --confirm-terminal --actor … --evidence …`.
- `reconcile-phase` moves a `create_pending` or `allocated` journal file to `cleanup_confirmed` with `manual_resolution`.
- `reconcile-idempotent` no-ops when the allocation is already `cleanup_confirmed` or `result_committed`.

## How to get to it (user POV)

- Hit a recovery block that names an allocation ID under `allocations/`.
- Confirm the exact create is terminal in the original provider account.
- Run `uv run --locked python benchpress.py reconcile --out <dir> --allocation <id> --confirm-terminal --actor '…' --evidence '…'`.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- `$BENCHPRESS_VERIFY_SCRATCH` and `$BENCHPRESS_VERIFY_EVIDENCE` are set from `launch.sh`.
- Offline: seed a scratch experiment with `experiment.json` and one `create_pending` allocation (helper does this).

- **Seed and reconcile.** Run `.cursor/skills/verify-benchpress/helpers/drive-reconcile.sh`.
- **Confirm phase.** The allocation JSON under evidence shows `phase: "cleanup_confirmed"` and a `manual_resolution` object with `actor`, `evidence`, and `recorded_at`.
- **Confirm exit.** Process exit code is `0`.
- **Proof.** Keep `$BENCHPRESS_VERIFY_EVIDENCE/reconcile-allocation/` (stdout, stderr, exit code, before/after allocation JSON).

## Gotchas

- Unknown allocation IDs fail. Do not delete journal files to bypass a block.
- Empty actor or evidence fails validation. A rerun or expired TTL alone is not valid evidence on a live experiment.
- Legacy cleanup-warning rows without an allocation journal still need `resolve-cleanup`, not `reconcile`.
- Automatic cloud recovery is provider-limited (see README); this feature only proves the attestation CLI and journal phase change.
