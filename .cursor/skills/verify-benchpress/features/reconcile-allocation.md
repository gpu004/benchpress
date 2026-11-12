# Reconcile allocation

Reconcile records operator evidence that a durable allocation journal entry is terminally cleaned when automatic recovery cannot finish. It advances the allocation phase to `cleanup_confirmed` without rewriting measurement history or calling providers.

## Sub-features

- `reconcile-cli` runs `benchpress.py reconcile --out <dir> --allocation <id> --confirm-terminal --actor … --evidence …`.
- `reconcile-phase` publishes the shared schema-2 receipt under `cleanup-resolutions/<allocation_id>.json`, then moves the journal to `cleanup_confirmed` with a `cleanup_receipt` reference.
- `reconcile-idempotent` reuses an existing valid receipt without changing its evidence. `resolve-cleanup` reuses that same receipt for a journaled attempt.

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
- **Confirm phase.** The allocation JSON shows `phase: "cleanup_confirmed"` and a `cleanup_receipt` reference. The referenced receipt contains `resolved_by`, `evidence`, and `resolved_at`.
- **Confirm exit.** Process exit code is `0`.
- **Proof.** Keep `$BENCHPRESS_VERIFY_EVIDENCE/reconcile-allocation/`, including the receipt, stdout, stderr, exit code, and before and after allocation JSON.

## Gotchas

- Unknown allocation IDs fail. Do not delete journal files to bypass a block.
- Empty actor or evidence fails validation. A rerun or expired TTL alone is not valid evidence on a live experiment.
- Legacy cleanup-warning rows without an allocation journal still need `resolve-cleanup`, not `reconcile`.
- Automatic cloud recovery is provider-limited (see README); this feature only proves the attestation CLI and journal phase change.
