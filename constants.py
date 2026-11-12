"""Shared harness constants (no I/O, signals, or executors)."""

WORKLOADS = ("one", "ten", "token", "cores", "disk", "net", "install")
PRIMARY_WORKLOAD = "one"
LEGACY_WORKLOADS = ("one", "ten", "token")
SERIES_BLOCKS = 30
RATIO_MIN_VERIFIED = SERIES_BLOCKS - 1
DEADLINE = 120
DEADLINE_MS = DEADLINE * 1000
WARM_RUNS = 3
EXEC_PROBE_RUNS = 5
BURST_DEADLINE = 180
ARTIFACT_KEY = {"tesser": "snapshot", "modal": "image", "e2b": "template", "namespace": "image"}
