# Unit and property tests

Unit and property tests exercise harness helpers without launching cloud boxes: mocked lifecycle, providers, allocation journal recovery, cleanup-resolution gating, report writers (temp dirs), and payload verification rules.

## Sub-features

- `unittest-modules` runs `python -m unittest test_benchpress test_providers test_allocation test_allocation_model test_cleanup`.
- `hegel-properties` runs `python test_properties.py` (hegel tests printed then executed).

## How to get to it (user POV)

- From the repo root, run the unittest modules listed above via `uv run --locked --group dev`.
- From the repo root, run `uv run --locked --group dev python test_properties.py`.

## Driving it with verify-benchpress

Preconditions:

- `doctor: READY`.
- Dev group installed (`launch.sh` uses `uv sync --group dev`).

- **Unit suite.** Run `.cursor/skills/verify-benchpress/helpers/drive-tests.sh` (or the same `unittest` module list). Exit code `0` and an `OK` summary appear in the captured transcript.
- **Property suite.** The same helper runs `uv run --locked --group dev python test_properties.py`. Stdout ends with `all property tests passed`.
- **Proof.** Keep `$BENCHPRESS_VERIFY_EVIDENCE/unit-and-property-tests/` transcripts and exit-code files.

## Gotchas

- Hegel is provided by the `dev` dependency group (`hegeltest`). A sync without `--group dev` fails imports.
- Do not treat these tests as a substitute for `report-from-fixture`, `resolve-cleanup`, `reconcile-allocation`, or cloud smoke: they mock providers and use temp dirs.
- `test_properties.py` is not a unittest module discovery target for hegel-marked functions; run the file directly.
