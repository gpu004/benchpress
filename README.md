# Benchpress

Benchpress measures how long a short command takes to return a verified result on Tesser, Modal, E2B, and Namespace. The primary comparison is one pass over a 32 MiB fixture on a fresh box. The runner also measures the same command on the running box, a ten-pass variant, and a command that returns a nonce. The guest uses Node 26.13.1 and returns a checksum, record count, and guest timings. The Modal, E2B, and Namespace images use Debian 13 Trixie. Tesser uses its managed Ubuntu base.

## Pilot results

On November 11, 2026, Tesser, Modal, and E2B each ran three workloads using Node 26.13.1. Each workload ran on a fresh box from a prepared image or snapshot, then again on that same running box. Fresh timings start at the launch request. Running timings start when the client sends the command. Both end when the client verifies the result.

Tesser timed commands used `tesser ssh` against a box started with `tesser make --snapshot`, so the local checkout was not synced into the timed path. An earlier same-day pilot that used `tesser exec` inflated Tesser’s running-box times and is superseded by these numbers.

| Provider | Token, fresh / running | One pass, fresh / running | Ten passes, fresh / running | Verified commands |
|---|---:|---:|---:|---:|
| Tesser Standard | 5.021 / 1.214 s | 5.307 / 1.297 s | 6.498 / 2.604 s | 6/6 |
| Modal Sandbox | 1.613 / 0.319 s | 1.555 / 0.519 s | 2.436 / 1.768 s | 6/6 |
| E2B | 2.832 / 0.129 s | 3.484 / 0.449 s | 4.739 / 3.021 s | 6/6 |
| Namespace | — | — | — | Launch rejected |

Guest compute time on the fresh ten-pass workload was 1.668 s on Tesser, 1.629 s on Modal, and 2.916 s on E2B. All timed pilot boxes were cleaned up. An existing Namespace box passed a smoke test, but the account's one-box limit blocked a fresh launch.

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

In the one-pass pilot, estimated compute cost per verified result was **$0.000099 for E2B**, **$0.000103 for Tesser**, and **$0.001084 for Modal**. Each estimate uses the full box lifecycle through cleanup, divided by the two verified commands. Modal’s client-observed lifecycle was about 33 s per box in this run, mostly after the verified commands, so its cost estimate is dominated by create and terminate time rather than the workload. These are list-rate estimates, not invoices.

## Set up and smoke-test

Use uv 0.13.7 and install the pinned SDKs with `uv sync --locked --python 3.15.2`. Install and sign in to the [Tesser CLI](https://tesser.sh/docs/quickstart), [Modal](https://modal.com/docs/guide/sandboxes), and the [Namespace Devbox CLI](https://namespace.so/blog/devbox-test-with-skills). For E2B, use `e2b auth login` or supply `E2B_API_KEY` in the runner process environment. Do not put credentials in the provider TOML or commit them.

```sh
uv run --locked python benchpress.py smoke tesser
uv run --locked python benchpress.py smoke modal
uv run --locked python benchpress.py smoke e2b
uv run --locked python benchpress.py smoke namespace --existing YOUR_DEVBOX_NAME
uv run --locked python benchpress.py smoke namespace
```

The existing Namespace box is retained. Each other smoke test uses a disposable box. Smoke tests verify command execution and cleanup. The Tesser smoke test uses `tesser exec` to check that the checkout sync puts `README.md` on the box. A pilot also verifies the prepared image, workload, and timing path.

## Prepare and run

Rebuild the artifacts before running with a new Node version. Commit the Tesser manifest and `payload/` before preparing Tesser. Run `uv run --locked python benchpress.py prepare PROVIDER` for each provider. It prints the artifact identifier and preparation time. Copy the returned values into a local `benchpress.local.toml`:

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

Record preparation charges separately when the provider exposes them. `benchpress.toml` includes list rates for the default configurations, including the product-specific Modal Sandbox rate and the Namespace S Devbox rate. Override `usd_per_hour` when your plan, size or region changes the rate. Omitted rates produce unknown costs. Use a unique experiment directory per set of artifact IDs. Keep images for the full experiment and remove them using their providers' tooling when finished.

For Tesser, the benchmark starts a box with `tesser make --snapshot` and runs the prepared payload with `tesser ssh <box_id> -- …`, which skips checkout sync for both fresh and running-box commands. Run a pilot before the formal comparison. You can limit it to available providers with `--providers modal namespace`.

```sh
uv run --locked python benchpress.py --config benchpress.local.toml run --pilot --out results/pilot
uv run --locked python benchpress.py report results/pilot
```

Use one fixed client in US East. Run the command below with the same arguments once each day on three consecutive Eastern calendar dates, starting on the chosen date. It runs ten randomized blocks per day. Resuming on the same date skips recorded attempts. Each block runs every provider and workload, with fresh and running-box commands capped at two minutes each.

```sh
uv run --locked python benchpress.py --config benchpress.local.toml run \
  --client-region us-east --start-date YYYY-MM-DD --out results/main
uv run --locked python benchpress.py report results/main
```

The JSONL records wall time, launch call time, guest file-read and compute timings, status, and machine details where available. A launch call can return before the box accepts commands, so some startup time can fall between the launch call and guest execution. CPU settings target two guest CPUs. Inspect `visible_cpus`, `available_parallelism`, CPU quota, memory and placement in the pilot before interpreting differences. Compare medians and tails alongside the verified completion rate. The report gives a paired block bootstrap interval for each provider versus Tesser on the primary workload when the series is complete and at least 29 of 30 attempts verified on each side. Cost is estimated over the full cold-plus-warm lifecycle, including failures with known costs. Host-level CPU allocation cannot be inferred from these samples.
