This bounded model checks `RecordedFailureBlocksLaunch` from [issue #1](https://github.com/gpu004/benchpress/issues/1). The issue's original model was not checked into this repository, so this is a new, scoped model of the saved-warning path.

With Java and the [TLA+ tools](https://github.com/tlaplus/tlaplus/releases) available, run from this directory:

```sh
java -cp /path/to/tla2tools.jar tlc2.TLC -metadir /path/to/scratch/fixed -config CleanupRestart.cfg CleanupRestart
java -cp /path/to/tla2tools.jar tlc2.TLC -metadir /path/to/scratch/broken -config WithoutStartupGuard.cfg CleanupRestart
```

The first configuration must pass. The second must fail `RecordedFailureBlocksLaunch`, with an allocation following a saved warning, a stop, and a restart without a resolution receipt.

The bounds are two allocations and two restart attempts. Both warning outcomes, confirmed terminal cleanup, a separate durable receipt, uncertain lookups, and a failed resolution write are represented. Allocation numbers stand for exact immutable saved-row identities. The startup transition corresponds to `locked_experiment`, and the resolution transition corresponds to `resolve-cleanup` under that same lock. The Python regressions exercise those implementations using child processes and persistent local fake boxes.

This checks an abstraction. It does not prove Python file I/O, provider ownership, manual evidence, crash recovery before a saved attempt, or burst scheduling. Already-submitted burst workers are covered separately in the regression tests.
