# Benchpress

Benchpress measures how long a short command takes to return a verified result on Tesser, Modal, E2B, and Namespace. The primary comparison is one pass over a 32 MiB fixture on a fresh box. The runner also measures the same command on the running box, a ten-pass variant, and a command that returns a nonce. The guest uses Node 22.23.4 and returns a checksum, record count, and guest timings.

## Set up and smoke-test

Use Python 3.12 and install the pinned SDKs with `uv sync --locked --python 3.12`. Install and sign in to the [Tesser CLI](https://tesser.sh/docs/quickstart), [Modal](https://modal.com/docs/guide/sandboxes), and the [Namespace Devbox CLI](https://namespace.so/blog/devbox-test-with-skills). Supply `E2B_API_KEY` in the runner process environment. Do not put credentials in the provider TOML or commit them.

```sh
uv run --locked python benchpress.py smoke tesser
uv run --locked python benchpress.py smoke modal
uv run --locked python benchpress.py smoke e2b
uv run --locked python benchpress.py smoke namespace --existing rose-derive-2tej
uv run --locked python benchpress.py smoke namespace
```

The existing Namespace box is retained. Each other smoke test uses a disposable box. Smoke tests verify command execution and cleanup, while a pilot also verifies the prepared image, workload, and timing path.

## Prepare and run

Commit the Tesser manifest and `payload/` before preparing Tesser. Run `uv run --locked python benchpress.py prepare PROVIDER` for each provider. It prints the artifact identifier and preparation time. Copy the returned values into a local `benchpress.local.toml`:

```toml
[tesser]
snapshot = "your-tesser-snapshot-id"

[modal]
image = "your-modal-image-id"

[e2b]
template = "your-e2b-template-alias"

[namespace]
image = "your-namespace-image-name"
```

Record preparation charges separately when the provider exposes them. `benchpress.toml` includes list rates for the default Tesser and Modal configurations. Override `usd_per_hour` when your plan, size or region changes the rate. Omitted rates produce unknown costs. Use a unique experiment directory per set of artifact IDs. Keep images for the full experiment and remove them using their providers' tooling when finished.

Run a pilot before the formal comparison. You can limit it to available providers with `--providers modal namespace`.

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
