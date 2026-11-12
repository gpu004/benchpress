#!/usr/bin/env bash
# Install pinned deps and allocate an isolated scratch + evidence directory.
# Benchpress is a short-lived CLI: there is no long-running server to keep alive.
# Stdout is only shell exports so callers can: eval "$(helpers/launch.sh)"
set -euo pipefail

# helpers/ -> verify-benchpress/ -> skills/ -> .cursor/ -> repo root
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)"
SKILL="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

RUN_ID="${BENCHPRESS_VERIFY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
SCRATCH="${BENCHPRESS_VERIFY_SCRATCH:-/tmp/benchpress-verify-$RUN_ID}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:-$SKILL/artifacts/$RUN_ID}"

mkdir -p "$SCRATCH" "$EVIDENCE"

command -v uv >/dev/null || {
  echo "launch: uv not on PATH" >&2
  exit 1
}

uv sync --locked --python 3.15.2 --group dev >&2

if command -v npm >/dev/null; then
  if [[ ! -d "$ROOT/node_modules" ]]; then
    npm ci >&2
  fi
fi

cat >"$EVIDENCE/launch.env" <<EOF
BENCHPRESS_VERIFY_RUN_ID=$RUN_ID
BENCHPRESS_VERIFY_SCRATCH=$SCRATCH
BENCHPRESS_VERIFY_EVIDENCE=$EVIDENCE
BENCHPRESS_VERIFY_ROOT=$ROOT
EOF

{
  echo "launch: READY"
  echo "RUN_ID=$RUN_ID"
  echo "SCRATCH=$SCRATCH"
  echo "EVIDENCE=$EVIDENCE"
} | tee "$EVIDENCE/launch.log" >&2

printf 'export BENCHPRESS_VERIFY_RUN_ID=%q\n' "$RUN_ID"
printf 'export BENCHPRESS_VERIFY_SCRATCH=%q\n' "$SCRATCH"
printf 'export BENCHPRESS_VERIFY_EVIDENCE=%q\n' "$EVIDENCE"
printf 'export BENCHPRESS_VERIFY_ROOT=%q\n' "$ROOT"
