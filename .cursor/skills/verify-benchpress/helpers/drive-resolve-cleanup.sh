#!/usr/bin/env bash
# Drive resolve-cleanup against a seeded offline cleanup-warning row.
set -euo pipefail

ROOT="${BENCHPRESS_VERIFY_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)}"
SCRATCH="${BENCHPRESS_VERIFY_SCRATCH:?set BENCHPRESS_VERIFY_SCRATCH (run helpers/launch.sh)}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:?set BENCHPRESS_VERIFY_EVIDENCE (run helpers/launch.sh)}"
cd "$ROOT"

EXPERIMENT="$SCRATCH/resolve-cleanup"
PROOF="$EVIDENCE/resolve-cleanup"
rm -rf "$EXPERIMENT"
mkdir -p "$EXPERIMENT" "$PROOF"

uv run --locked python - <<'PY' "$EXPERIMENT"
import json, sys
from pathlib import Path
from allocation import cleanup_attempt_id
from scenarios import write_attempt

out = Path(sys.argv[1])
row = {
    "block": 0,
    "workload": "token",
    "provider": "modal",
    "scenario": "standard",
    "name": "benchpress-verify-cleanup",
    "box_id": "sb-verify-cleanup",
    "cold": {"status": "ok"},
    "cleanup": "failed",
}
with (out / "attempts.jsonl").open("w") as stream:
    write_attempt(stream, row)
(out / "attempt_id.txt").write_text(cleanup_attempt_id(row) + "\n")
(out / "row.json").write_text(json.dumps(row, sort_keys=True) + "\n")
print("seeded", cleanup_attempt_id(row))
PY

ATTEMPT_ID="$(tr -d '\n' <"$EXPERIMENT/attempt_id.txt")"
cp "$EXPERIMENT/attempts.jsonl" "$PROOF/attempts.before.jsonl"
BEFORE_BYTES="$(wc -c <"$EXPERIMENT/attempts.jsonl" | tr -d ' ')"

set +e
uv run --locked python benchpress.py resolve-cleanup \
  --out "$EXPERIMENT" \
  --attempt "$ATTEMPT_ID" \
  --resolved-by "verify-benchpress" \
  --allocation-scope "verify scratch modal account / benchpress-verify-cleanup" \
  --confirmed-outcome removed \
  --evidence "offline verification seed; ownership attested in scratch only" \
  >"$PROOF/stdout.txt" 2>"$PROOF/stderr.txt"
status=$?
set -e
echo "$status" >"$PROOF/exit_code.txt"

[[ "$status" -eq 0 ]] || {
  echo "drive-resolve-cleanup: non-zero exit; see $PROOF/stderr.txt" >&2
  exit 1
}

AFTER_BYTES="$(wc -c <"$EXPERIMENT/attempts.jsonl" | tr -d ' ')"
[[ "$BEFORE_BYTES" == "$AFTER_BYTES" ]] || {
  echo "drive-resolve-cleanup: attempts.jsonl mutated" >&2
  exit 1
}
cmp -s "$PROOF/attempts.before.jsonl" "$EXPERIMENT/attempts.jsonl" || {
  echo "drive-resolve-cleanup: attempts.jsonl content changed" >&2
  exit 1
}

RECEIPT="$EXPERIMENT/cleanup-resolutions/${ATTEMPT_ID}.json"
[[ -f "$RECEIPT" ]] || {
  echo "drive-resolve-cleanup: missing receipt $RECEIPT" >&2
  exit 1
}
cp "$RECEIPT" "$PROOF/receipt.json"
cp "$EXPERIMENT/attempts.jsonl" "$PROOF/attempts.after.jsonl"

uv run --locked python - <<'PY' "$PROOF/receipt.json" "$ATTEMPT_ID"
import json, sys
receipt = json.loads(open(sys.argv[1]).read())
attempt_id = sys.argv[2]
assert receipt["schema"] == 2
assert receipt["subject"] == {"attempt_id": attempt_id}
assert receipt["attempt_id"] == attempt_id
assert receipt["confirmed_outcome"] == "removed"
assert receipt["allocation"] == {
    "provider": "modal",
    "name": "benchpress-verify-cleanup",
    "box_id": "sb-verify-cleanup",
}
for key in ("resolved_by", "resolved_at", "allocation_scope", "evidence"):
    assert isinstance(receipt[key], str) and receipt[key].strip(), key
print("drive-resolve-cleanup: receipt checks ok")
PY

echo "drive-resolve-cleanup: PASS evidence=$PROOF"
