#!/usr/bin/env bash
# Drive reconcile against a seeded offline create_pending allocation journal entry.
set -euo pipefail

ROOT="${BENCHPRESS_VERIFY_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)}"
SCRATCH="${BENCHPRESS_VERIFY_SCRATCH:?set BENCHPRESS_VERIFY_SCRATCH (run helpers/launch.sh)}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:?set BENCHPRESS_VERIFY_EVIDENCE (run helpers/launch.sh)}"
cd "$ROOT"

EXPERIMENT="$SCRATCH/reconcile-allocation"
PROOF="$EVIDENCE/reconcile-allocation"
rm -rf "$EXPERIMENT"
mkdir -p "$EXPERIMENT" "$PROOF"

uv run --locked python - <<'PY' "$EXPERIMENT"
import json, sys
from pathlib import Path
from allocation import AllocationJournal, durable_json

out = Path(sys.argv[1])
manifest = {"experiment_id": "verify-reconcile-offline"}
durable_json(out / "experiment.json", manifest)
journal = AllocationJournal(out, manifest)
entry = journal.begin("modal", {"app": "benchpress-verify"}, "standard", "token", 0)
(out / "allocation_id.txt").write_text(entry.state["allocation_id"] + "\n")
(out / "allocation.before.json").write_text(json.dumps(entry.state, indent=2, sort_keys=True) + "\n")
print("seeded", entry.state["allocation_id"])
PY

ALLOCATION_ID="$(tr -d '\n' <"$EXPERIMENT/allocation_id.txt")"
cp "$EXPERIMENT/allocation.before.json" "$PROOF/allocation.before.json"

set +e
uv run --locked python benchpress.py reconcile \
  --out "$EXPERIMENT" \
  --allocation "$ALLOCATION_ID" \
  --confirm-terminal \
  --actor "verify-benchpress" \
  --evidence "offline verification seed; create confirmed terminal in scratch only" \
  >"$PROOF/stdout.txt" 2>"$PROOF/stderr.txt"
status=$?
set -e
echo "$status" >"$PROOF/exit_code.txt"

[[ "$status" -eq 0 ]] || {
  echo "drive-reconcile: non-zero exit; see $PROOF/stderr.txt" >&2
  exit 1
}

AFTER="$EXPERIMENT/allocations/${ALLOCATION_ID}.json"
[[ -f "$AFTER" ]] || {
  echo "drive-reconcile: missing allocation file $AFTER" >&2
  exit 1
}
cp "$AFTER" "$PROOF/allocation.after.json"
cp "$EXPERIMENT/cleanup-resolutions/${ALLOCATION_ID}.json" "$PROOF/receipt.json"

uv run --locked python - <<'PY' "$PROOF/allocation.before.json" "$PROOF/allocation.after.json" "$PROOF/receipt.json"
import json, sys
before = json.loads(open(sys.argv[1]).read())
after = json.loads(open(sys.argv[2]).read())
assert before["phase"] == "create_pending"
assert after["phase"] == "cleanup_confirmed"
assert after["allocation_id"] == before["allocation_id"]
receipt = json.loads(open(sys.argv[3]).read())
assert after["cleanup_receipt"] == f'cleanup-resolutions/{before["allocation_id"]}.json'
assert receipt["schema"] == 2
assert receipt["subject"]["allocation_id"] == before["allocation_id"]
assert receipt["resolved_by"] == "verify-benchpress"
assert "scratch" in receipt["evidence"]
assert receipt["resolved_at"]
assert after["cleanup_confirmed_at"]
print("drive-reconcile: allocation phase checks ok")
PY

echo "drive-reconcile: PASS evidence=$PROOF"
