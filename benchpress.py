import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
import tomllib
import uuid
from zoneinfo import ZoneInfo

from providers import PAYLOAD, PROVIDERS, ROOT, Sandbox, e2b_key, prepare

NODE = (ROOT / '.node-version').read_text().strip()
WORKLOADS = ('one', 'ten', 'token')
DEADLINE = 120
CHECKSUMS = {
    'one': '51fa3c6d8375a696a933cb5d5554c5606b773b2558335d0bc291173541b07b5c',
    'ten': '86ce8f3d1e87bfe6089060f88d7832a8619d0c7a34657b2d989edb4dfce80b58',
}
ARTIFACT_KEY = {'tesser': 'snapshot', 'modal': 'image', 'e2b': 'template', 'namespace': 'image'}
SMOKE = 'test "$(uname -s)" = Linux && printf "BENCHPRESS_SMOKE %s %s\\n" "$1" "$((6 * 7))"'


def now():
    return datetime.now(timezone.utc).isoformat()


def error_text(error):
    message = str(error).replace(os.environ.get('E2B_API_KEY') or '\x00', '<redacted>')
    message = re.sub(r'\b(?:sk_)?e2b_[A-Za-z0-9_-]{16,}\b', '<redacted>', message)
    return f'{type(error).__name__}: {message}'[-1800:]


def settings(path):
    config = tomllib.loads((ROOT / 'benchpress.toml').read_text())
    if path:
        for provider, overrides in tomllib.loads(Path(path).read_text()).items():
            if provider not in PROVIDERS:
                raise ValueError(f'Unknown provider in config: {provider}')
            config[provider].update(overrides)
    return config


def parse_marker(output, marker):
    lines = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise ValueError(f'Expected one {marker.strip()} line, found {len(lines)}')
    return json.loads(lines[0])


def verify(output, workload, token):
    result = parse_marker(output, 'BENCHPRESS_RESULT ')
    expected = {'workload': workload, 'token': token, 'node': NODE}
    if workload != 'token':
        expected.update(bytes=32 * 1024**2, records=524288 * (10 if workload == 'ten' else 1),
                        checksum=CHECKSUMS[workload])
        for metric in ('read_ms', 'compute_ms'):
            value = result.get(metric)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError(f'Invalid guest duration: {metric}')
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError(f'Unexpected {key} in guest result')
    return result


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError(f'{seconds}s deadline exceeded')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def trial(provider, config, workload, block, *, existing=None, smoke=False):
    token = uuid.uuid4().hex
    name = f'benchpress-{token[:20]}'
    row = {'provider': provider, 'workload': workload, 'block': block, 'name': name,
           'started_at': now(), 'cold': {'status': 'not_run'}, 'warm': {'status': 'not_run'}}
    try:
        with deadline(30):
            box = Sandbox(provider, config, name, smoke=smoke, existing=existing)
    except Exception as error:
        row['cold'] = {'status': 'setup_error', 'error': error_text(error)}
        return row

    row['client_version'] = box.version
    cold_started = time.monotonic()

    def run(mode):
        started = time.monotonic() if mode == 'warm' else cold_started
        sample = row[mode] = {'status': 'running'}
        try:
            with deadline(DEADLINE):
                if mode == 'cold':
                    box.launch(DEADLINE)
                    sample['launch_ms'] = (time.monotonic() - started) * 1000
                nonce = f'{token}-{mode}'
                if smoke:
                    script = 'test -f README.md && ' + SMOKE if provider == 'tesser' else SMOKE
                    argv = ['sh', '-c', script, 'sh', nonce]
                else:
                    argv = box.payload('run.mjs', workload, nonce)
                remaining = max(.01, DEADLINE - (time.monotonic() - started))
                output = box.exec(argv, remaining)
                if smoke:
                    if output.splitlines().count(f'BENCHPRESS_SMOKE {nonce} 42') != 1:
                        raise ValueError('Smoke command did not return the expected nonce and value')
                else:
                    sample['guest'] = verify(output, workload, nonce)
                sample['status'] = 'ok'
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            sample.update(status='timeout', error=error_text(error))
        except Exception as error:
            sample.update(status='failed', error=error_text(error))
        finally:
            sample['wall_ms'] = (time.monotonic() - started) * 1000

    try:
        run('cold')
        if row['cold']['status'] == 'ok' and not smoke:
            run('warm')
            try:
                with deadline(15):
                    row['machine'] = parse_marker(box.exec(box.payload('machine.mjs'), 15), 'BENCHPRESS_MACHINE ')
                    row['machine']['region_label'] = config.get('region_label')
                    row['machine']['requested_site'] = config.get('site') or config.get('region')
            except Exception as error:
                row['machine_error'] = error_text(error)
            try:
                with deadline(15):
                    row['provider_placement'] = box.placement()
            except Exception as error:
                row['placement_error'] = error_text(error)
    finally:
        try:
            with deadline(35):
                row['cleanup'] = box.close()
        except Exception as error:
            row.update(cleanup='failed', cleanup_error=error_text(error))
        row['box_id'] = box.id
        row['lifecycle_seconds'] = time.monotonic() - cold_started
        if 'usd_per_hour' in config and row['cleanup'] == 'removed':
            row['estimated_usd'] = row['lifecycle_seconds'] / 3600 * config['usd_per_hour']
    return row


def schedule(providers, seed, blocks=30):
    rng = random.Random(seed)
    for block in range(blocks):
        workloads = list(WORKLOADS)
        rng.shuffle(workloads)
        for workload in workloads:
            order = list(providers)
            rng.shuffle(order)
            for provider in order:
                yield block, workload, provider


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def write_attempt(stream, row):
    stream.write(json.dumps(row, allow_nan=False) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def fingerprint():
    digest = hashlib.sha256()
    paths = [ROOT / name for name in ('benchpress.py', 'providers.py', '.node-version', 'uv.lock')]
    paths.append(ROOT / '.claude/skills/tesser/benchpress.toml')
    paths += [path for path in PAYLOAD.iterdir() if path.is_file()]
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def run_series(args, config):
    providers = args.providers or list(PROVIDERS)
    if len(set(providers)) != len(providers):
        raise ValueError('List each provider at most once')
    for provider in providers:
        if not config[provider].get(ARTIFACT_KEY[provider]):
            raise ValueError(f'Prepare {provider} and set {ARTIFACT_KEY[provider]} in the config')
    if 'e2b' in providers:
        e2b_key()
    day = 0
    if not args.pilot:
        if args.client_region != 'us-east' or args.start_date is None:
            raise ValueError('Use --client-region us-east and --start-date on the fixed US East client')
        today = datetime.now(ZoneInfo('America/New_York')).date()
        day = (today - args.start_date).days
        if day not in range(3):
            raise ValueError('Run once per Eastern calendar day from --start-date through day 3')

    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / 'experiment.json'
    attempts_path = args.out / 'attempts.jsonl'
    manifest = {'schema': 1, 'pilot': args.pilot, 'start_date': str(args.start_date),
                'client_region': args.client_region, 'providers': providers, 'deadline_seconds': DEADLINE,
                'client': hashlib.sha256(socket.gethostname().encode()).hexdigest(),
                'node': NODE, 'source_sha256': fingerprint(), 'seed': args.seed,
                'config': {name: config[name] for name in providers}}
    with (args.out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest_path.exists():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('Experiment configuration changed; choose a new results directory')
        else:
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        done = {(r['block'], r['workload'], r['provider']) for r in read_rows(attempts_path)}
        with attempts_path.open('a') as stream:
            for block, workload, provider in schedule(providers, args.seed, 1 if args.pilot else 30):
                if block // 10 != day or (block, workload, provider) in done:
                    continue
                row = trial(provider, config[provider], workload, block)
                write_attempt(stream, row)
                print(f'{block:02} {provider:9} {workload:5} {row["cold"]["status"]}', flush=True)
                if row['cold']['status'] == 'setup_error' or row.get('cleanup') in ('failed', 'creation_unconfirmed'):
                    raise RuntimeError(f'Inspect {attempts_path} before continuing; setup or cleanup needs attention')


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def interval(pairs):
    rng = random.Random(7256)
    by_day = defaultdict(list)
    for block, pair in pairs.items():
        by_day[block // 10].append(pair)
    draws = []
    for _ in range(5000):
        sample = [pair for group in by_day.values() for pair in rng.choices(group, k=len(group))]
        left, right = zip(*sample)
        draws.append(statistics.median(left) / statistics.median(right))
    return percentile(draws, .025), percentile(draws, .975)


def report(path):
    manifest = json.loads((path / 'experiment.json').read_text())
    rows = read_rows(path / 'attempts.jsonl')
    groups = defaultdict(list)
    for row in rows:
        groups[row['provider'], row['workload']].append(row)
    target = 1 if manifest['pilot'] else 30
    complete = all(sorted(r['block'] for r in groups[p, w]) == list(range(target))
                   for p in manifest['providers'] for w in WORKLOADS)
    print('# Benchpress results\n')
    if manifest['pilot'] or not complete:
        print('Pilot or incomplete experiment. Do not use these samples for a public comparison.\n')
    print('| Provider | Work | Phase | Attempts | Verified | p50 ms | p90 ms | Est. USD per verified result |')
    print('|---|---|---|---:|---:|---:|---:|---:|')
    for (provider, workload), group in sorted(groups.items()):
        cost = '—'
        verified = sum(row[m]['status'] == 'ok' for row in group for m in ('cold', 'warm'))
        if verified and all('estimated_usd' in row for row in group):
            cost = f'{sum(row["estimated_usd"] for row in group) / verified:.6f}'
        for phase in ('cold', 'warm'):
            values = [row[phase]['wall_ms'] for row in group if row[phase]['status'] == 'ok']
            p50 = f'{statistics.median(values):.2f}' if values else '—'
            p90 = f'{percentile(values, .90):.2f}' if values else '—'
            print(f'| {provider} | {workload} | {phase} | {len(group)} | '
                  f'{len(values)}/{len(group)} | {p50} | {p90} | {cost if phase == "cold" else "—"} |')
    print('\nLatency percentiles use verified results. Skipped warm runs count against warm completion.')
    print('Cost estimates use client-observed lifecycle time and configured usage rates; '
          'they exclude plan fees and image preparation. A dash means the cost is unknown.\n')
    for provider in manifest['providers']:
        phases = [r for r in groups[provider, 'one'] if r['cold']['status'] == 'ok']
        if phases:
            launch = statistics.median(r['cold']['launch_ms'] for r in phases)
            read = statistics.median(r['cold']['guest']['read_ms'] for r in phases)
            compute = statistics.median(r['cold']['guest']['compute_ms'] for r in phases)
            print(f'- {provider} fresh one-pass medians: launch call {launch:.2f} ms, guest read {read:.2f} ms, guest compute {compute:.2f} ms.')
    if complete and not manifest['pilot'] and 'tesser' in manifest['providers']:
        base = {r['block']: r['cold']['wall_ms'] for r in groups['tesser', 'one'] if r['cold']['status'] == 'ok'}
        for provider in manifest['providers']:
            if provider == 'tesser':
                continue
            other = {r['block']: r['cold']['wall_ms'] for r in groups[provider, 'one'] if r['cold']['status'] == 'ok'}
            paired = {block: (other[block], base[block]) for block in base.keys() & other.keys()}
            if min(len(base), len(other)) < 29 or len(paired) < 28:
                print(f'- {provider} / Tesser: insufficient verified pairs for a headline ratio.')
                continue
            left, right = zip(*paired.values())
            ratio = statistics.median(left) / statistics.median(right)
            low, high = interval(paired)
            print(f'- {provider} / Tesser, fresh one-pass: {ratio:.2f}× (95% interval {low:.2f}–{high:.2f}×, {len(paired)} paired blocks).')


def main():
    parser = argparse.ArgumentParser(description='Compare quick remote execution on isolated cloud boxes')
    parser.add_argument('--config', type=Path, help='TOML overrides for providers')
    sub = parser.add_subparsers(dest='action', required=True)
    smoke = sub.add_parser('smoke')
    smoke.add_argument('provider', choices=PROVIDERS)
    smoke.add_argument('--existing', help='Run against an existing Namespace box')
    prep = sub.add_parser('prepare')
    prep.add_argument('provider', choices=PROVIDERS)
    run = sub.add_parser('run')
    run.add_argument('--out', type=Path, required=True)
    run.add_argument('--providers', nargs='+', choices=PROVIDERS)
    run.add_argument('--pilot', action='store_true')
    run.add_argument('--client-region')
    run.add_argument('--start-date', type=date.fromisoformat)
    run.add_argument('--seed', type=int, default=7256)
    sub.add_parser('report').add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        config = settings(args.config)
        if args.action == 'report':
            report(args.path)
        elif args.action == 'smoke':
            if args.existing and args.provider != 'namespace':
                raise ValueError('--existing is for Namespace')
            row = trial(args.provider, config[args.provider], 'smoke', None,
                        existing=args.existing, smoke=True)
            print(json.dumps(row, indent=2))
            return int(row['cold']['status'] != 'ok' or row.get('cleanup') not in ('removed', 'existing_box_kept'))
        elif args.action == 'prepare':
            started = time.monotonic()
            name = f'benchpress-{fingerprint()[:10]}-{uuid.uuid4().hex[:8]}'
            result = prepare(args.provider, config[args.provider], name)
            print(json.dumps({'provider': args.provider, **result,
                              'prepare_seconds': time.monotonic() - started}))
        else:
            run_series(args, config)
    except (ValueError, OSError, RuntimeError) as error:
        print(error_text(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
