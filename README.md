# Benchpress

Benchpress measures how long a short command takes to return a verified result on Tesser, Modal, E2B, and Namespace. The primary comparison is one pass over a 32 MiB fixture on a fresh box. The runner also measures the same command on the running box, a ten-pass variant, a nonce command, a two-worker CPU parse, a 64 MiB disk write/read, a pinned package download, and a local npm install from a prepared tarball. The guest uses Node 26.13.1 and returns a checksum plus guest timings. The Modal, E2B, and Namespace images use Debian 13 Trixie. Tesser uses its managed Ubuntu base. Formal runs use a single US East client; the report states that single-region limitation.

## How the harness stays honest

- Every result is verified. A per-run nonce, the record count, the byte count, and a SHA-256 checksum are all checked, so a fast wrong answer or a stale box counts as a failure, not a win.
- Each run is a full lifecycle. It times launch, time to ready, and workload to a verified result, then runs warm commands on the same box. The runner journals allocation intent before launch, attempts cleanup, and blocks on restart if an allocation or its cleanup remains uncertain.
- The run order is designed. Providers and workloads are shuffled within each block from a fixed seed, and the plan of 10 blocks a day for 3 days is fixed in advance.
- Runs are reproducible. Tool and package versions are pinned, the source is fingerprinted, a run will not resume if the configuration changed, and results are written safely to disk.
- Failures count. Timeouts lower the completion rate, wall latency treats failures as the deadline, and the report gives no headline ratio for a pilot or an incomplete series.
- It records more than wall time. Guest compute time is separated from total time, command-send overhead is probed on the running box, and hardware details are collected after the timed commands so they do not slow them. Primary cost uses billable time through the last measured command; harness metadata and cleanup wait are reported separately.
- The README is honest. It says the pilot numbers are single observations and that hardware differs between providers.
- Secrets are handled carefully. E2B keys are removed from error text, and local configuration is kept out of git.

## Pilot results

On November 11, 2026, artifacts were re-prepared and Tesser, Modal, and E2B each ran three workloads using Node 26.13.1. Each workload ran on a fresh box from a prepared image or snapshot, then again on that same running box. Fresh timings start at the launch request. Running timings start when the client sends the command. Both end when the client verifies the result.

Tesser ran the timed commands with `tesser ssh` against boxes started with `tesser make --snapshot`, which skips checkout sync for both command runs. The earlier pilot used `tesser exec` and included checkout sync in both timings. Sanitized raw rows for the corrected pilot are in [`fixtures/pilot/`](fixtures/pilot/) (`attempts.jsonl` and `experiment.json`; live box and artifact IDs replaced).

| Provider | Token, fresh / running | One pass, fresh / running | Ten passes, fresh / running | Verified commands |
|---|---:|---:|---:|---:|
| Tesser Standard | 15.230 / 1.252 s | 5.813 / 1.329 s | 7.510 / 2.628 s | 6/6 |
| Modal Sandbox | 1.259 / 0.329 s | 1.096 / 0.363 s | 3.343 / 2.402 s | 6/6 |
| E2B | 3.269 / 0.143 s | 2.331 / 0.398 s | 5.417 / 3.041 s | 6/6 |
| Namespace | — | — | — | Launch rejected |

Guest compute time on the fresh ten-pass workload was 1.636 s on Tesser, 2.282 s on Modal, and 3.232 s on E2B. Tesser’s token fresh sample spent 13.222 s in the launch call alone; treat that cell as a launch-variance observation, not a typical token latency. All timed pilot boxes were cleaned up. An existing Namespace box still held the account's one-box limit, so Namespace was omitted from the timed pilot after a rejected launch.

Each timing is one observation. The providers ran from the same client in one interleaved pilot, with different machine configurations and placement. The pilot does not establish a typical speedup, CPU oversubscription, or build and test performance. The planned three-day series is pending, and Boat was not timed.

## Pricing comparison

USD list rates checked November 11, 2026. The hour columns show compute usage before minimums, credits, storage, or network charges.

| Product | Configuration | USD/hour | 100 h | 500 h |
|---|---|---:|---:|---:|
| [Boat Small](https://boat.dev/) | 2 vCPU, 4 GB | $0.0180 | $1.80 | $9.00 |
| [Boat Default](https://boat.dev/) | 4 vCPU, 8 GB | $0.0360 | $3.60 | $18.00 |
| [Tesser Standard](https://tesser.sh/pricing) | 2 vCPU, 8 GB | $0.0800 | $8.00 | $40.00 |
| [E2B](https://e2b.dev/pricing) | 2 vCPU, 4 GiB | $0.1656 | $16.56 | $82.80 |
| [Amp Orb Small](https://ampcode.com/docs/orbs/sizes-and-costs) | 2 CPU, 4 GB | $0.1700 | $17.00 | $85.00 |
| [Modal Sandbox](https://modal.com/products/sandboxes#active-resource-sandbox-pricing) | 1 physical core, 4 GiB | $0.2380 | $23.80 | $118.98 |
| [Namespace Devbox S](https://namespace.so/pricing.md#devbox-sizes) | Up to 4 vCPU, 8 GB | $0.2400 | $24.00 | $120.00 |

Boat's $20 monthly minimum covers either example for both sizes. Tesser Pro includes $20 of usage in its $20 monthly fee. Amp's $20 Megawatt plan lists 45,000 hosted orb minutes. The Modal row uses the Sandbox rate for the reservation we tested. [Boat pricing](https://boat.dev/) · [Tesser pricing](https://tesser.sh/pricing) · [Amp plans](https://ampcode.com/pricing) · [Modal Sandbox billing](https://modal.com/docs/guide/sandbox-resources)

In the one-pass pilot, estimated compute cost per verified result was **$0.000071 for E2B**, **$0.000114 for Tesser**, and **$0.001051 for Modal**. Those pilot estimates used the full client-observed lifecycle through cleanup, divided by the two verified commands. Modal’s lifecycle was about 32 s per box in that run, mostly after the verified commands, so the figure is teardown-dominated. Newer reports separate billable time through the last measured command from harness metadata and cleanup wait. These are list-rate estimates, not invoices.

## Set up and smoke-test

Use uv 0.13.7 and install the pinned SDKs with `uv sync --locked --python 3.15.2`. Install and sign in to the [Tesser CLI](https://tesser.sh/docs/quickstart), [Modal](https://modal.com/docs/guide/sandboxes), and the [Namespace Devbox CLI](https://namespace.so/blog/devbox-test-with-skills). For E2B, use `e2b auth login` or supply `E2B_API_KEY` in the runner process environment. Do not put credentials in the provider TOML or commit them.

```sh
uv run --locked ruff format .
uv run --locked ruff check .
uv run --locked ty check
npm ci
npm run lint
npm run check
```

```sh
uv run --locked python benchpress.py smoke tesser
uv run --locked python benchpress.py smoke modal
uv run --locked python benchpress.py smoke e2b
uv run --locked python benchpress.py smoke namespace --existing YOUR_DEVBOX_NAME
uv run --locked python benchpress.py smoke namespace
```

The existing Namespace box is retained. Each other smoke test uses a disposable box. Smoke tests verify command execution and cleanup. The Tesser smoke test uses `tesser exec` to check that the checkout sync puts `README.md` on the box. A pilot also verifies the prepared image, workload, and timing path.

## Prepare and run

Rebuild the artifacts before running with a new Node version. Commit the Tesser manifest and `payload/` before preparing Tesser. Run `uv run --locked python benchpress.py prepare PROVIDER` for each provider. Add `--variant large` to bake a 256 MiB filler into the image or snapshot so pull and restore cost is visible. It prints the artifact identifier and preparation time. Copy the returned values into a local `benchpress.local.toml`:

```toml
[tesser]
snapshot = "your-tesser-snapshot-id"

[modal]
image = "your-modal-image-id"

[e2b]
template = "your-e2b-template-id"

[namespace]
image = "your-namespace-image-name"
```

Record preparation charges separately when the provider exposes them. `benchpress.toml` includes list rates and `price_date` for the default configurations, including the product-specific Modal Sandbox rate and the Namespace S Devbox rate. Override `usd_per_hour` or `price_date` when your plan, size, region, or checked rate changes. Omitted rates produce unknown costs. Use a unique experiment directory per set of artifact IDs. Keep images for the full experiment and remove them using their providers' tooling when finished.

For Tesser, the benchmark starts a box with `tesser make --snapshot` and runs the prepared payload with `tesser ssh <box_id> -- …`, which skips checkout sync for both fresh and running-box commands. Run a pilot before the formal comparison. You can limit it to available providers with `--providers modal namespace`.

Tesser builds the large variant from a temporary branch in a local clone of the committed payload. Use the returned snapshot ID in the benchmark config.

```sh
uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --out results/pilot
uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --mode burst --concurrency 8 --out results/burst
uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --mode resume --out results/resume
uv run --locked python benchpress.py report results/pilot
```

Use one fixed client in US East. The harness does not run a second client region; every report states that single-region limit. Run the command below with the same arguments once each day on three consecutive Eastern calendar dates, starting on the chosen date. It runs ten randomized blocks per day. Resuming on the same date skips recorded attempts. Each block runs every provider and selected workload, with fresh and running-box commands capped at two minutes each. Default workloads are `one ten token cores disk net install`. Pass `--workloads` to narrow a run. `--mode burst --concurrency N` launches N boxes at once per provider. `--mode resume` times sleep/pause then wake/resume on Tesser, Modal, and E2B; Namespace is recorded as unsupported. Modal resume requires a workspace with access to experimental persistent sandboxes.

Saved `cleanup="failed"` and `cleanup="creation_unconfirmed"` rows block further launches in that experiment, including after a restart or on a later day. The error lists the affected attempt ID, provider, allocation name, and known box ID. Verify ownership and remove that exact allocation, or obtain authoritative evidence that it is terminally absent. Then record the manual resolution:

```sh
uv run --locked python benchpress.py resolve-cleanup --out results/main \
  --attempt FULL_ATTEMPT_ID_FROM_ERROR --resolved-by YOUR_OPERATOR_ID \
  --allocation-scope 'provider account/project and exact allocation ID or creation request' \
  --confirmed-outcome removed \
  --evidence 'ownership evidence and provider deletion/terminal-state receipt'
```

Use `--confirmed-outcome absent` only for a confirmed terminal absence. A lost creation response, failed lookup, ambiguous match, empty list, or elapsed TTL alone does not establish absence. Keep the experiment blocked until the exact creation is accounted for. This command records an operator attestation; it does not query providers or delete boxes. It requires the full attempt ID and nonempty operator, scope, and evidence fields. Receipts are persisted separately under `cleanup-resolutions/` with the allocation identity and UTC time. Original measurements remain unchanged and recorded attempts remain skipped. Run and resolution commands use the same experiment lock. Burst runs drain and save their already-submitted workers before stopping on a cleanup warning; existing burst outputs still require a new directory. Allocations interrupted before an attempt row was saved are outside this guard, as tracked in [issue #2](https://github.com/gpu004/benchpress/issues/2).

```sh
uv run --locked python benchpress.py --config benchpress.local.toml run \
  --client-region us-east --start-date YYYY-MM-DD --out results/main
uv run --locked python benchpress.py report results/main
```

The JSONL records wall time split into launch call, time-to-ready, and workload, plus guest timings, status, scenario, billable vs full-lifecycle seconds, and machine details where available. A launch call can return before the box accepts commands; the runner probes with a ping until ready so that wait is not folded into the workload. After each successful cold command the runner issues five back-to-back no-op pings on the running box to measure command-send overhead (CLI/SSH per command on Tesser and Namespace versus reused SDK connections on Modal and E2B), then three warm workload commands; the report shows that ping overhead plus `warm-first` (not steady state) and `warm-median` across the three warm commands. CPU settings target two guest CPUs; the `cores` workload is what exercises both. Inspect `visible_cpus`, `available_parallelism`, CPU quota, memory and placement in the pilot before interpreting differences. Wall latency counts timeout, failure, setup error, and skipped warm attempts as the 120 s deadline so unreliable providers cannot look faster by dropping samples. Compare medians, p10–p90 spread, and per-day medians alongside the verified completion rate; p90 is labeled indicative because with 30 samples it rests on about three observations. The report stamps the source fingerprint, observed date range, configured `price_date`, client region, and the single-region limitation; prints wall and guest-compute medians with day-blocked bootstrap 95% intervals; and writes `report.md`, `report.json`, `report.csv`, and SVG strip plots under `plots/`. It also gives a paired block bootstrap interval for each provider versus Tesser on the primary one-pass workload when the series is complete and at least 29 of 30 attempts verified on each side with at least 29 paired blocks. Primary cost uses billable seconds through the last measured command and excludes harness metadata collection and cleanup wait; full-lifecycle cost is reported beside it. Host-level CPU allocation cannot be inferred from these samples.


### Interrupted allocations and restart recovery

Each scheduled disposable allocation has a durable `allocations/<allocation_id>.json`
record. The manifest carries a persistent experiment ID. The allocation record
contains its scheduled key, generation, remote lookup identity, non-secret scope
references, phase, timestamps, and returned box ID. State writes fsync a temporary
file, replace the state file, then fsync its directory before dispatching create.
Burst workers write separate allocation files; only the coordinator appends results.
Smoke and prepare commands are ad-hoc operations outside this experiment journal.

Startup holds the experiment lock while validating all allocation records and
recovering incomplete creates or cleanup. It does this before reading result rows
or building the schedule. `create_pending` and `allocated` block new work until
cleanup has a confirmed terminal outcome. `cleanup_confirmed` can then advance to
`result_committed`. Recovery can repeat after another crash.

Automatic recovery has deliberately limited provider coverage:

| Provider | Recovery contract |
| --- | --- |
| Modal standard/burst | Resolve the recorded ID, or the exact name within the recorded app, including terminated sandboxes. Verify the allocation tag, durably record the ID, then terminate that exact sandbox with `wait=True`. An empty lookup or ownership mismatch blocks. |
| Modal resume | Recover by ID and allocation tag. A lost response without a recorded ID requires manual reconciliation because persistent creates send no name. |
| E2B | A recorded ID plus matching `benchpress_trial` metadata permits deletion of that exact sandbox. A missing ID or 404 requires manual reconciliation; metadata discovery does not establish create idempotency or absence in the original account. |
| Namespace and Tesser | Manual reconciliation. Namespace's create name is recorded; Tesser currently sends no stable lookup identity. Their lookup/terminal-absence contracts have not been established for automatic recovery. |

These checks use the active provider credentials. Preserve the original account
and environment when recovering. No credentials are copied into allocation
records. The Modal behavior follows the pinned SDK's
[Sandbox contract](https://modal.com/docs/reference/modal.Sandbox), including unique
names within an app, ownership tags, and repeatable termination. This change does
not claim exactly-once provider creation or test live cloud recovery.

A blocked error identifies the allocation record, scheduled key, remote identity,
and known box ID. Reconcile that exact allocation in the original provider account,
including any create request still in flight. Confirm that it can no longer become
live before recording evidence:

```sh
uv run --locked python benchpress.py reconcile --out results/main \
  --allocation ALLOCATION_ID --confirm-terminal --actor 'operator name' \
  --evidence 'provider/account reference, exact allocation, terminal outcome and request evidence'
```

This command records the operator, evidence, and timestamp under the same lock.
It is an operator attestation, not a provider lookup. It does not delete a box or
rewrite measurement history. A rerun or an expired TTL alone is insufficient
evidence. Do not delete pending records or use another results directory to bypass
a block. Older cleanup-warning rows without an allocation journal remain blocked
and require manual inspection; this command cannot resolve those legacy rows.

After confirmed cleanup, an interrupted allocation without a durably staged result
gets exactly one `cold.status="interrupted"` row. It counts as a failed attempt and
uses the report's deadline censoring. The scheduled key is consumed; there is no
automatic retry of that key within this experiment, and recovered boxes are never
reused as cold samples. Missing timings and costs remain unknown. A result staged
in the journal before the crash is preserved as measured, including original
cleanup failures, and matched to JSONL by allocation ID to avoid duplicate samples.
Cleanup receipts remain in the allocation files.

Malformed journal state, mismatched results, duplicate keys, missing receipts, and
partial trailing JSONL rows block scheduling. Recovery runs before result parsing,
so a torn row cannot bypass cleanup. A torn row is preserved along with the staged
result for operator reconciliation; the runner never truncates it automatically.
Back up both files before repairing measurement data under the experiment lock.

Intent fsync happens before lifecycle timing starts. `launch_ms` still measures
only the provider call. Persisting the returned ID happens afterward and contributes
to cold wall time, the remaining cold deadline, and billable/lifecycle time. Its
cost is exposed as `journal_seconds_during_cold`. Cleanup-confirmation and result
fsync happen after lifecycle timing. Normal `finally` cleanup remains in place.

Run the local regression suite without cloud credentials:

```sh
uv run --locked --group dev python -m unittest test_allocation test_allocation_model -v
```

`test_allocation.py` adapts the [issue's safe reproducer](https://github.com/gpu004/benchpress/issues/1).
It kills only child runners around intent dispatch, remote acceptance, returned-ID
persistence, readiness, workload, cleanup, result append/flush/fsync, and final
journal completion. Fake provider state survives each process, and a peer box must
remain untouched. Tests also cover repeated recovery interruption, delayed or
failed lookup, malformed state, manual-resolution write failure, experiment locks,
healthy skipping, and burst recovery. Fsync tests kill immediately before/after
the syscall; they do not simulate storage hardware losing acknowledged writes.

`test_allocation_model.py` is an executable bounded model of
`UnrecordedBoxBlocksLaunch`, with two scheduled keys and up to two restarts. It
includes late create acceptance and torn result writes, and detects the original
missing-journal restart bug when its safety gate is removed. The issue's original
TLA+ source was not included in this repository. This model checks local ordering,
not cloud contracts or arbitrary concurrent executions.
