#!/usr/bin/env bash
# Drive the local unit + property test suite (no cloud providers).
set -euo pipefail

ROOT="${BENCHPRESS_VERIFY_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:?set BENCHPRESS_VERIFY_EVIDENCE (run helpers/launch.sh)}"
cd "$ROOT"

PROOF="$EVIDENCE/unit-and-property-tests"
mkdir -p "$PROOF"

set +e
uv run --locked --group dev python -m unittest \
  test_benchpress test_providers test_allocation test_allocation_model test_cleanup -v \
  >"$PROOF/unittest.stdout.txt" 2>"$PROOF/unittest.stderr.txt"
unit_status=$?
set -e
echo "$unit_status" >"$PROOF/unittest.exit_code.txt"

set +e
uv run --locked --group dev python test_properties.py \
  >"$PROOF/properties.stdout.txt" 2>"$PROOF/properties.stderr.txt"
prop_status=$?
set -e
echo "$prop_status" >"$PROOF/properties.exit_code.txt"

[[ "$unit_status" -eq 0 ]] || {
  echo "drive-tests: unittest failed; see $PROOF/unittest.stderr.txt" >&2
  exit 1
}
[[ "$prop_status" -eq 0 ]] || {
  echo "drive-tests: property tests failed; see $PROOF/properties.stderr.txt" >&2
  exit 1
}

grep -q 'OK' "$PROOF/unittest.stderr.txt" \
  || grep -q 'OK' "$PROOF/unittest.stdout.txt" \
  || { echo "drive-tests: unittest OK marker missing" >&2; exit 1; }
grep -q 'all property tests passed' "$PROOF/properties.stdout.txt" \
  || { echo "drive-tests: property pass marker missing" >&2; exit 1; }

echo "drive-tests: PASS evidence=$PROOF"
