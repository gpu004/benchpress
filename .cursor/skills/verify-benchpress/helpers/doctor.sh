#!/usr/bin/env bash
# Read-only health check for a benchpress verification run.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)"
cd "$ROOT"

fail() {
  echo "doctor: FAIL: $*" >&2
  exit 1
}

ok() {
  echo "doctor: ok: $*"
}

command -v uv >/dev/null || fail "uv not on PATH"
uv_version="$(uv --version | awk '{print $2}')"
[[ "$uv_version" == "0.13.7" ]] || fail "uv $uv_version found; need 0.13.7"
ok "uv $uv_version"

[[ -d "$ROOT/.venv" ]] || fail ".venv missing; run helpers/launch.sh"
ok ".venv present"

python_version="$(uv run --locked python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
[[ "$python_version" == "3.15.2" ]] || fail "python $python_version; need 3.15.2 via uv"
ok "python $python_version"

uv run --locked --group dev python -c 'import benchpress, report, lifecycle, providers, stats, scenarios, constants, hegel' \
  || fail "python imports failed (need uv sync --group dev)"
ok "benchpress modules import"

[[ -f "$ROOT/fixtures/pilot/attempts.jsonl" ]] || fail "fixtures/pilot/attempts.jsonl missing"
[[ -f "$ROOT/fixtures/pilot/experiment.json" ]] || fail "fixtures/pilot/experiment.json missing"
ok "fixtures/pilot present"

help_out="$(uv run --locked python benchpress.py --help)"
printf '%s\n' "$help_out" | grep -q '{smoke,prepare,run,report,resolve-cleanup,reconcile}' \
  || fail "benchpress --help missing expected subcommands"
ok "CLI subcommands visible"

if [[ -n "${BENCHPRESS_VERIFY_SCRATCH:-}" ]]; then
  [[ -d "$BENCHPRESS_VERIFY_SCRATCH" ]] || fail "scratch dir missing: $BENCHPRESS_VERIFY_SCRATCH"
  ok "scratch $BENCHPRESS_VERIFY_SCRATCH"
fi

if [[ -n "${BENCHPRESS_VERIFY_EVIDENCE:-}" ]]; then
  [[ -d "$BENCHPRESS_VERIFY_EVIDENCE" ]] || fail "evidence dir missing: $BENCHPRESS_VERIFY_EVIDENCE"
  ok "evidence $BENCHPRESS_VERIFY_EVIDENCE"
fi

echo "doctor: READY"
