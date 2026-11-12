--------------------------- MODULE CleanupRestart ---------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS MaxAllocations, MaxRestarts, StartupGuard
VARIABLES phase, current, saved, terminal, receipts, restarts, writeFailures,
          uncertainLookups, unsafeLaunch
vars == <<phase, current, saved, terminal, receipts, restarts, writeFailures,
          uncertainLookups, unsafeLaunch>>
Allocations == 1..MaxAllocations
Warnings == {"failed", "creation_unconfirmed"}
Unresolved == {a \in Allocations : saved[a] \in Warnings} \ receipts

Init == /\ phase = "ready"
        /\ current = 0
        /\ saved = [a \in Allocations |-> "none"]
        /\ terminal = {}
        /\ receipts = {}
        /\ restarts = 0
        /\ writeFailures = 0
        /\ uncertainLookups = 0
        /\ unsafeLaunch = FALSE

Launch == /\ phase = "ready"
          /\ current < MaxAllocations
          /\ current' = current + 1
          /\ phase' = "allocated"
          /\ unsafeLaunch' = (unsafeLaunch \/ (Unresolved # {}))
          /\ UNCHANGED <<saved, terminal, receipts, restarts, writeFailures, uncertainLookups>>

Save(outcome) == /\ phase = "allocated"
                 /\ saved' = [saved EXCEPT ![current] = outcome]
                 /\ terminal' = IF outcome = "removed" THEN terminal \cup {current} ELSE terminal
                 /\ phase' = IF outcome \in Warnings THEN "stopped" ELSE "ready"
                 /\ UNCHANGED <<current, receipts, restarts, writeFailures,
                                uncertainLookups, unsafeLaunch>>

Restart == /\ phase \in {"stopped", "blocked"}
           /\ restarts < MaxRestarts
           /\ restarts' = restarts + 1
           /\ phase' = IF StartupGuard /\ Unresolved # {} THEN "blocked" ELSE "ready"
           /\ UNCHANGED <<current, saved, terminal, receipts, writeFailures,
                          uncertainLookups, unsafeLaunch>>

\* An operator verifies ownership and a terminal outcome for this exact allocation.
ConfirmTerminal(a) == /\ phase \in {"stopped", "blocked"}
                      /\ a \in Unresolved \ terminal
                      /\ terminal' = terminal \cup {a}
                      /\ UNCHANGED <<phase, current, saved, receipts, restarts,
                                     writeFailures, uncertainLookups, unsafeLaunch>>

\* Cleanup alone does not unblock startup. A durable receipt is also required.
RecordResolution(a) == /\ phase \in {"stopped", "blocked"}
                       /\ a \in Unresolved \cap terminal
                       /\ receipts' = receipts \cup {a}
                       /\ UNCHANGED <<phase, current, saved, terminal, restarts,
                                      writeFailures, uncertainLookups, unsafeLaunch>>

FailResolutionWrite == /\ phase \in {"stopped", "blocked"}
                       /\ Unresolved \cap terminal # {}
                       /\ writeFailures = 0
                       /\ writeFailures' = 1
                       /\ UNCHANGED <<phase, current, saved, terminal, receipts,
                                      restarts, uncertainLookups, unsafeLaunch>>

UncertainLookup == /\ phase \in {"stopped", "blocked"}
                   /\ Unresolved # {}
                   /\ uncertainLookups = 0
                   /\ uncertainLookups' = 1
                   /\ UNCHANGED <<phase, current, saved, terminal, receipts,
                                  restarts, writeFailures, unsafeLaunch>>

Next == \/ Launch
        \/ \E outcome \in Warnings \cup {"removed"} : Save(outcome)
        \/ Restart
        \/ \E a \in Allocations : ConfirmTerminal(a) \/ RecordResolution(a)
        \/ FailResolutionWrite
        \/ UncertainLookup

Spec == Init /\ [][Next]_vars
RecordedFailureBlocksLaunch == ~unsafeLaunch
ReceiptsRequireTerminalEvidence == receipts \subseteq terminal
=============================================================================
