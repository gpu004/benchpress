#!/usr/bin/env bash
# Drive the user-facing `report` path against the sanitized pilot fixture.
set -euo pipefail

ROOT="${BENCHPRESS_VERIFY_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)}"
SCRATCH="${BENCHPRESS_VERIFY_SCRATCH:?set BENCHPRESS_VERIFY_SCRATCH (run helpers/launch.sh)}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:?set BENCHPRESS_VERIFY_EVIDENCE (run helpers/launch.sh)}"
cd "$ROOT"

EXPERIMENT="$SCRATCH/report-fixture"
PROOF="$EVIDENCE/report-from-fixture"
rm -rf "$EXPERIMENT"
mkdir -p "$EXPERIMENT" "$PROOF"

cp "$ROOT/fixtures/pilot/attempts.jsonl" "$EXPERIMENT/attempts.jsonl"
cp "$ROOT/fixtures/pilot/experiment.json" "$EXPERIMENT/experiment.json"

set +e
uv run --locked python benchpress.py report "$EXPERIMENT" \
  >"$PROOF/stdout.txt" 2>"$PROOF/stderr.txt"
status=$?
set -e
echo "$status" >"$PROOF/exit_code.txt"

for name in report.md report.json report.csv; do
  [[ -f "$EXPERIMENT/$name" ]] || {
    echo "drive-report: missing $EXPERIMENT/$name" >&2
    exit 1
  }
  cp "$EXPERIMENT/$name" "$PROOF/$name"
done

mkdir -p "$PROOF/plots"
if [[ -d "$EXPERIMENT/plots" ]]; then
  cp -R "$EXPERIMENT/plots/." "$PROOF/plots/"
fi

grep -q '^# Benchpress results$' "$PROOF/report.md" \
  || { echo "drive-report: report.md missing title" >&2; exit 1; }
grep -q 'Pilot or incomplete experiment' "$PROOF/report.md" \
  || { echo "drive-report: expected pilot/incomplete banner" >&2; exit 1; }
grep -q '| tesser | one | cold |' "$PROOF/report.md" \
  || { echo "drive-report: missing tesser/one/cold row" >&2; exit 1; }
grep -q '5813.44' "$PROOF/report.md" \
  || { echo "drive-report: missing expected tesser one-pass cold p50" >&2; exit 1; }
grep -q 'plots/one-cold-wall.svg' "$PROOF/report.md" \
  || { echo "drive-report: missing plot listing" >&2; exit 1; }
[[ -f "$PROOF/plots/one-cold-wall.svg" ]] \
  || { echo "drive-report: missing SVG plot artifact" >&2; exit 1; }
[[ "$(cat "$PROOF/exit_code.txt")" == "0" ]] \
  || { echo "drive-report: non-zero exit" >&2; exit 1; }

python3 - <<'PY' "$PROOF/report.json"
import json, sys
payload = json.loads(open(sys.argv[1]).read())
assert payload["source_sha256"].startswith("e42b9c4c"), payload["source_sha256"]
assert payload["scenario"] == "standard"
assert payload["pilot"] is True
assert any(p.endswith("one-cold-wall.svg") for p in payload["plots"])
print("drive-report: report.json checks ok")
PY

echo "drive-report: PASS evidence=$PROOF"
