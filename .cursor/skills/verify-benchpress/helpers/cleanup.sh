#!/usr/bin/env bash
# Remove scratch state created by this verification run. Never delete evidence.
set -euo pipefail

SCRATCH="${BENCHPRESS_VERIFY_SCRATCH:-}"
EVIDENCE="${BENCHPRESS_VERIFY_EVIDENCE:-}"

if [[ -z "$SCRATCH" ]]; then
  echo "cleanup: BENCHPRESS_VERIFY_SCRATCH unset; nothing to remove" >&2
  exit 0
fi

case "$SCRATCH" in
  /tmp/benchpress-verify-*)
    if [[ -d "$SCRATCH" ]]; then
      rm -rf "$SCRATCH"
      echo "cleanup: removed scratch $SCRATCH"
    else
      echo "cleanup: scratch already absent ($SCRATCH)"
    fi
    ;;
  *)
    echo "cleanup: refusing to remove unexpected scratch path: $SCRATCH" >&2
    exit 1
    ;;
esac

if [[ -n "$EVIDENCE" && -d "$EVIDENCE" ]]; then
  echo "cleanup: retained evidence $EVIDENCE"
else
  echo "cleanup: warning: evidence dir missing ($EVIDENCE)" >&2
fi
