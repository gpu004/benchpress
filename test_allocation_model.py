"""Executable bounded model for issue #2's UnrecordedBoxBlocksLaunch rule.

Two scheduled keys, two restarts, one outstanding create, durable journal phases,
late create acceptance, torn/visible/synced rows, and repeatable cleanup. Remote
lookup is abstracted to a positive owned match or no progress. Manual resolution
requires a settled create and terminal absence. This checks the ordering rule,
not SDK contracts, storage hardware, arbitrary concurrency, or provider expiry.
The original issue's standalone TLA+ source is not in this checkout.
"""

import unittest
from collections import deque
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class State:
    phases: tuple = ("none", "none")
    rows: tuple = ("absent", "absent")
    live: frozenset = frozenset()
    requests: frozenset = frozenset()
    pc: str = "ready"
    current: int = 0
    restarts: int = 0
    unsafe_launch: bool = False


def changed(values, index, value):
    return tuple(value if i == index else item for i, item in enumerate(values))


def successors(state, *, legacy=False):
    s, i = state, state.current
    # Requests already dispatched can finish while the runner is dead.
    for pending in s.requests:
        yield "remote accept", replace(s, requests=s.requests - {pending}, live=s.live | {pending})
    if s.pc not in ("stopped", "ready") and s.restarts < 2:
        yield "kill", replace(s, pc="stopped")
    if s.pc == "stopped" and s.restarts < 2:
        if legacy:
            phases = tuple(
                "none" if phase in ("pending", "allocated") else phase for phase in s.phases
            )
            yield (
                "legacy restart without journal",
                replace(s, phases=phases, pc="ready", restarts=s.restarts + 1),
            )
        else:
            yield "restart under lock", replace(s, pc="recover", restarts=s.restarts + 1)
    if s.pc == "ready":
        if any(phase not in ("none", "committed") for phase in s.phases):
            return
        for key, phase in enumerate(s.phases):
            if phase == "none":
                yield (
                    "fsync intent",
                    replace(
                        s, phases=changed(s.phases, key, "pending"), current=key, pc="dispatch"
                    ),
                )
                break
    elif s.pc == "dispatch":
        yield (
            "dispatch create",
            replace(
                s,
                requests=s.requests | {i},
                pc="response",
                unsafe_launch=bool(s.live) or s.unsafe_launch,
            ),
        )
    elif s.pc == "response" and i in s.live and i not in s.requests:
        yield "fsync returned ID", replace(s, phases=changed(s.phases, i, "allocated"), pc="work")
    elif s.pc == "work":
        yield "finish measurement", replace(s, pc="delete")
    elif s.pc == "delete":
        yield "remove exact owned allocation", replace(s, live=s.live - {i}, pc="confirm")
    elif s.pc == "confirm":
        yield (
            "fsync cleanup receipt",
            replace(s, phases=changed(s.phases, i, "cleaned"), pc="stage"),
        )
    elif s.pc == "stage":
        yield "fsync result in journal", replace(s, pc="append")
    elif s.pc == "append":
        yield "partial append", replace(s, rows=changed(s.rows, i, "partial"), pc="flush")
    elif s.pc == "flush":
        yield "flush complete row", replace(s, rows=changed(s.rows, i, "visible"), pc="sync")
    elif s.pc == "sync":
        yield "fsync row", replace(s, rows=changed(s.rows, i, "synced"), pc="complete")
    elif s.pc == "complete":
        yield (
            "fsync result committed",
            replace(s, phases=changed(s.phases, i, "committed"), pc="ready"),
        )
    elif s.pc == "recover":
        for key, phase in enumerate(s.phases):
            if phase in ("pending", "allocated"):
                if key in s.live and key not in s.requests:
                    yield (
                        "positive owned lookup and fsync ID",
                        replace(
                            s, phases=changed(s.phases, key, "allocated"), current=key, pc="delete"
                        ),
                    )
                elif key not in s.requests:
                    # Exact terminal absence or explicit operator evidence. Empty
                    # discovery alone has no transition, and never clears intent.
                    yield (
                        "confirmed terminal outcome",
                        replace(s, phases=changed(s.phases, key, "cleaned")),
                    )
                return
        for key, phase in enumerate(s.phases):
            if phase == "cleaned":
                if s.rows[key] == "partial":
                    return
                pc = {"absent": "stage", "visible": "sync", "synced": "complete"}[s.rows[key]]
                yield "reconcile result by allocation ID", replace(s, current=key, pc=pc)
                return
        yield "recovery complete", replace(s, pc="ready")


def explore(*, legacy=False):
    initial = State()
    queue = deque([initial])
    paths = {initial: ()}
    edges = 0
    while queue:
        state = queue.popleft()
        if state.unsafe_launch:
            return len(paths), edges, paths[state]
        for action, following in successors(state, legacy=legacy):
            edges += 1
            if following not in paths:
                paths[following] = (*paths[state], action)
                queue.append(following)
    return len(paths), edges, None


class AllocationModelTests(unittest.TestCase):
    def test_unrecorded_box_blocks_launch(self):
        states, edges, violation = explore()
        self.assertIsNone(violation)
        self.assertGreater(states, 100)
        self.assertGreater(edges, states)
        print(f"UnrecordedBoxBlocksLaunch: {states} states, {edges} transitions, no violation")

    def test_model_detects_the_original_missing_journal_bug(self):
        _, _, violation = explore(legacy=True)
        self.assertIsNotNone(violation)
        self.assertIn("kill", violation)
        self.assertIn("legacy restart without journal", violation)
        self.assertEqual(violation[-1], "dispatch create")


if __name__ == "__main__":
    unittest.main()
