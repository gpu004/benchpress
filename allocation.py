"""Durable allocation state. Callers hold the experiment lock, including recovery."""

import json
import os
import uuid
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile

from constants import WORKLOADS
from providers import PROVIDERS, allocation_identity, recover_allocation

PHASES = ("create_pending", "allocated", "cleanup_confirmed", "result_committed")
KEY_FIELDS = ("block", "workload", "provider", "scenario")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, value):
    """A failed write leaves the previous state intact; dispatch follows directory fsync."""
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", dir=path.parent, prefix=".pending-", delete=False) as out:
            temporary = out.name
            json.dump(value, out, allow_nan=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def durable_directory(path):
    if path.exists():
        return
    durable_directory(path.parent)
    path.mkdir()
    sync_directory(path.parent)


def attempt_key(row):
    return tuple(
        row.get(field, "standard" if field == "scenario" else None) for field in KEY_FIELDS
    )


class RecoveryBlocked(RuntimeError):
    pass


class Allocation:
    def __init__(self, journal, state):
        self.journal = journal
        self.state = state
        self.path = journal.path / (state["allocation_id"] + ".json")

    def save(self, **changes):
        state = {**self.state, **changes, "updated_at": timestamp()}
        durable_json(self.path, state)
        self.state = state

    def allocated(self, box_id):
        if not isinstance(box_id, str) or not box_id:
            raise RecoveryBlocked(f"Allocation {self.path} has no returned box ID")
        if self.state["box_id"] not in (None, box_id):
            raise RecoveryBlocked(f"Allocation {self.path} changed its box ID")
        self.save(phase="allocated", box_id=box_id, allocated_at=timestamp())

    def cleaned(self, **receipt):
        self.save(phase="cleanup_confirmed", cleanup_confirmed_at=timestamp(), **receipt)

    def blocked(self, reason):
        state = self.state
        return RecoveryBlocked(
            f"Allocation recovery blocked: {self.path}; attempt={state['attempt_key']}; "
            f"box_id={state['box_id']!r}; identity={state['provider_identity']}; {reason}. "
            "Reconcile this exact allocation and any in-flight create in its provider account, "
            "then use benchpress.py reconcile --out DIR --allocation ID --confirm-terminal "
            "--actor NAME --evidence 'provider evidence'. Do not delete the journal or retry "
            "in another directory while the allocation may still be live."
        )


class AllocationJournal:
    def __init__(self, root, manifest):
        self.path = root / "allocations"
        self.experiment_id = manifest["experiment_id"]
        if not self.path.exists():
            self.path.mkdir()
            sync_directory(root)
        self.entries = []
        keys = set()
        for path in sorted(self.path.glob("*.json")):
            try:
                state = json.loads(path.read_text())
                self.validate(state, path)
                key = attempt_key(state["attempt_key"])
                if key in keys:
                    raise ValueError("multiple allocations for one scheduled attempt")
                keys.add(key)
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise RecoveryBlocked(
                    f"Malformed allocation state {path}: {error}; scheduling blocked"
                ) from error
            self.entries.append(Allocation(self, state))

    def validate(self, state, path):
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise ValueError("unsupported allocation schema")
        if state["experiment_id"] != self.experiment_id:
            raise ValueError("experiment ID mismatch")
        allocation_id = state["allocation_id"]
        if uuid.UUID(hex=allocation_id).hex != allocation_id or path.stem != allocation_id:
            raise ValueError("allocation ID mismatch")
        if type(state["generation"]) is not int or state["generation"] != 1:
            raise ValueError("invalid generation")
        key = state["attempt_key"]
        if (
            not isinstance(key, dict)
            or set(key) != set(KEY_FIELDS)
            or type(key["block"]) is not int
            or key["block"] < 0
        ):
            raise ValueError("invalid attempt key")
        if key["provider"] not in PROVIDERS or key["workload"] not in WORKLOADS:
            raise ValueError("invalid provider/workload")
        if key["scenario"] not in ("standard", "resume", "burst"):
            raise ValueError("invalid scenario")
        if state["name"] != "benchpress-" + allocation_id:
            raise ValueError("invalid remote name")
        if not isinstance(state["provider_scope"], dict):
            raise ValueError("invalid provider scope")
        identity = allocation_identity(
            key["provider"], state["provider_scope"], state["name"], key["scenario"]
        )
        if state["provider_identity"] != identity:
            raise ValueError("provider identity mismatch")
        if state["phase"] not in PHASES:
            raise ValueError("invalid phase")
        if state["box_id"] is not None and (
            not isinstance(state["box_id"], str) or not state["box_id"]
        ):
            raise ValueError("invalid box ID")
        if state["phase"] == "allocated" and state["box_id"] is None:
            raise ValueError("allocated without a box ID")
        for field in ("created_at", "updated_at"):
            datetime.fromisoformat(state[field])
        if state["phase"] in ("cleanup_confirmed", "result_committed"):
            datetime.fromisoformat(state["cleanup_confirmed_at"])
        if state.get("result") is not None:
            self.validate_result(state, state["result"])
        if state["phase"] == "result_committed" and state.get("result") is None:
            raise ValueError("committed allocation without a result")

    @staticmethod
    def validate_result(state, row):
        if (
            not isinstance(row, dict)
            or row.get("allocation_id") != state["allocation_id"]
            or row.get("experiment_id") != state["experiment_id"]
            or attempt_key(row) != attempt_key(state["attempt_key"])
            or not isinstance(row.get("cold", {}).get("status"), str)
        ):
            raise ValueError("result does not match allocation")

    def begin(self, provider, config, scenario, workload, block):
        key = dict(zip(KEY_FIELDS, (block, workload, provider, scenario)))
        if any(
            attempt_key(entry.state["attempt_key"]) == attempt_key(key) for entry in self.entries
        ):
            raise RecoveryBlocked(f"Scheduled attempt already has an allocation: {key}")
        allocation_id = uuid.uuid4().hex
        name = "benchpress-" + allocation_id
        # Only non-secret provider configuration is retained here.
        scope = {
            field: config[field]
            for field in ("app", "region", "site", "image", "snapshot", "template", "size")
            if field in config
        }
        state = {
            "schema": 1,
            "experiment_id": self.experiment_id,
            "attempt_key": key,
            "allocation_id": allocation_id,
            "generation": 1,
            "name": name,
            "provider_scope": scope,
            "provider_identity": allocation_identity(provider, scope, name, scenario),
            "phase": "create_pending",
            "box_id": None,
            "created_at": timestamp(),
            "result": None,
        }
        entry = Allocation(self, state)
        entry.save()
        self.entries.append(entry)
        return entry

    def recover(self):
        """Finish cleanup before reading possibly torn results or building any schedule."""
        blocked = []
        for entry in self.entries:
            if entry.state["phase"] in ("cleanup_confirmed", "result_committed"):
                continue
            result = entry.state.get("result")
            saved_cleanup_warning = isinstance(result, dict) and result.get("cleanup") in (
                "failed",
                "creation_unconfirmed",
            )
            try:
                if result is None and not entry.state.get("interrupted_at"):
                    entry.save(interrupted_at=timestamp())
                recover_allocation(entry)
                entry.cleaned(recovered_at=timestamp())
            except Exception as error:
                if saved_cleanup_warning:
                    # Keep the saved warning for resolve-cleanup when automatic recovery cannot
                    # verify this exact allocation. Interrupted creates without a result still hard-block.
                    continue
                blocked.append(str(entry.blocked(str(error))))
        if blocked:
            raise RecoveryBlocked("\n".join(blocked))

    def commit(self, stream, row, write_attempt):
        allocation_id = row.get("allocation_id")
        entry = next(
            (entry for entry in self.entries if entry.state["allocation_id"] == allocation_id), None
        )
        if entry:
            self.validate_result(entry.state, row)
            entry.save(result=row)
        write_attempt(stream, row)
        if entry and entry.state["phase"] == "cleanup_confirmed":
            entry.save(phase="result_committed", result_committed_at=timestamp())

    def reconcile_results(self, rows, stream, write_attempt):
        by_id = {}
        by_key = {}
        for row in rows:
            if (
                type(row.get("block")) is not int
                or row.get("provider") not in PROVIDERS
                or row.get("workload") not in WORKLOADS
                or row.get("scenario", "standard") not in ("standard", "resume", "burst")
                or not isinstance(row.get("cold"), dict)
                or not isinstance(row["cold"].get("status"), str)
            ):
                raise RecoveryBlocked("Malformed attempt row; scheduling blocked")
            key = attempt_key(row)
            if key in by_key:
                raise RecoveryBlocked("Duplicate scheduled attempt in results; scheduling blocked")
            by_key[key] = row
            if allocation_id := row.get("allocation_id"):
                if not any(entry.state["allocation_id"] == allocation_id for entry in self.entries):
                    raise RecoveryBlocked(
                        f"Missing journal for result {allocation_id}; scheduling blocked"
                    )
                by_id[allocation_id] = row
        for entry in self.entries:
            state = entry.state
            saved = by_id.get(state["allocation_id"])
            if saved is not None:
                if saved != state.get("result"):
                    raise entry.blocked("saved result differs from journal")
                if state["phase"] == "result_committed":
                    continue
                # Sync a complete row that survived a crash before the writer's fsync.
                stream.flush()
                os.fsync(stream.fileno())
                sync_directory(self.path.parent)
                entry.save(phase="result_committed", result_committed_at=timestamp())
                continue
            if state["phase"] == "result_committed":
                raise entry.blocked("committed result is missing from attempts.jsonl")
            if attempt_key(state["attempt_key"]) in by_key:
                raise entry.blocked("another result uses this scheduled key")
            row = state.get("result") or {
                **state["attempt_key"],
                "experiment_id": self.experiment_id,
                "allocation_id": state["allocation_id"],
                "name": state["name"],
                "started_at": state["created_at"],
                "box_id": state["box_id"],
                "cold": {
                    "status": "interrupted",
                    "error": "Runner stopped before result persistence",
                },
                "warm": {"status": "not_run"},
                "cleanup": "removed",
            }
            if state.get("result") is None:
                entry.save(interrupted_at=timestamp())
            self.commit(stream, row, write_attempt)
            rows.append(row)
        # Older experiments have no allocation receipts to resolve these warnings.
        for row in rows:
            if row.get("cleanup") in ("failed", "creation_unconfirmed") and not row.get(
                "allocation_id"
            ):
                raise RecoveryBlocked(
                    f"Legacy cleanup warning for {attempt_key(row)}, box_id={row.get('box_id')!r}; manually reconcile before continuing"
                )
        return rows


def reconcile_manual(root, allocation_id, *, actor, evidence):
    """Record an operator's terminal-outcome evidence without rewriting measurements."""
    import fcntl

    if not actor.strip() or not evidence.strip():
        raise ValueError("Manual reconciliation needs an actor and provider evidence")
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads((root / "experiment.json").read_text())
        journal = AllocationJournal(root, manifest)
        entry = next(
            (item for item in journal.entries if item.state["allocation_id"] == allocation_id), None
        )
        if entry is None:
            raise ValueError("Unknown allocation ID")
        if entry.state["phase"] in ("cleanup_confirmed", "result_committed"):
            return
        entry.cleaned(
            manual_resolution={"actor": actor, "evidence": evidence, "recorded_at": timestamp()}
        )
