import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, open, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { Worker } from 'node:worker_threads';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const [workload, token] = process.argv.slice(2);
if (!token) throw new Error('Missing token');

const here = dirname(fileURLToPath(import.meta.url));
const fixtureUrl = new URL('./fixture.ndjson', import.meta.url);
const ONE_CHECKSUM = '51fa3c6d8375a696a933cb5d5554c5606b773b2558335d0bc291173541b07b5c';
const NET_URL = process.env.BENCHPRESS_NET_URL
  || 'https://registry.npmjs.org/is-number/-/is-number-7.0.0.tgz';
const NET_SHA256 = process.env.BENCHPRESS_NET_SHA256
  || '7b75c1057198cf97696909a9bee176c9c5e9bcb5b03bf3ecef2f484defadd51e';
const WORKERS = 2;

const result = { workload, token, node: process.versions.node };

function finish(extra = {}) {
  Object.assign(result, extra);
  console.log('BENCHPRESS_RESULT ' + JSON.stringify(result));
}

/** @param {number} passes */
async function runPasses(passes) {
  const readStarted = performance.now();
  const fixture = readFileSync(fixtureUrl);
  const readFinished = performance.now();
  const hash = createHash('sha256');
  let records = 0;
  for (let pass = 0; pass < passes; pass++) {
    const totals = new Map();
    for (const line of fixture.toString('ascii').trimEnd().split('\n')) {
      const { key, value } = JSON.parse(line);
      totals.set(key, (totals.get(key) ?? 0) + value);
      records++;
    }
    hash.update(JSON.stringify([...totals].sort(([a], [b]) => a - b)) + '\n');
  }
  finish({
    bytes: fixture.length, records, checksum: hash.digest('hex'),
    read_ms: readFinished - readStarted, compute_ms: performance.now() - readFinished,
  });
}

async function runCores() {
  const fixturePath = fileURLToPath(fixtureUrl);
  const started = performance.now();
  const workers = Array.from({ length: WORKERS }, () => new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./cores-worker.mjs', import.meta.url), {
      workerData: { fixturePath },
    });
    worker.once('message', resolve);
    worker.once('error', reject);
    worker.once('exit', (code) => {
      if (code !== 0) reject(new Error(`Worker exited ${code}`));
    });
  }));
  const messages = await Promise.all(workers);
  const computeMs = performance.now() - started;
  for (const message of messages) {
    if (message.checksum !== ONE_CHECKSUM) {
      throw new Error(`Unexpected worker checksum ${message.checksum}`);
    }
  }
  const checksum = createHash('sha256').update(`${WORKERS}:${ONE_CHECKSUM}\n`).digest('hex');
  finish({
    workers: WORKERS, bytes: messages[0].bytes, records: messages[0].records * WORKERS,
    checksum, compute_ms: computeMs,
  });
}

async function runDisk() {
  const bytes = 64 * 1024 * 1024;
  const path = join(tmpdir(), `benchpress-disk-${token}`);
  const block = Buffer.alloc(1024 * 1024);
  for (let i = 0; i < block.length; i++) block[i] = (i * 31 + 17) & 0xff;
  const writeStarted = performance.now();
  const handle = await open(path, 'w');
  let fsyncMs;
  try {
    for (let offset = 0; offset < bytes; offset += block.length) {
      const slice = Buffer.from(block);
      slice[0] = (offset / block.length) & 0xff;
      await handle.write(slice);
    }
    const fsyncStarted = performance.now();
    await handle.sync();
    fsyncMs = performance.now() - fsyncStarted;
  } finally {
    await handle.close();
  }
  const writeMs = performance.now() - writeStarted;
  const readStarted = performance.now();
  const data = await readFile(path);
  const readMs = performance.now() - readStarted;
  const checksum = createHash('sha256').update(data).digest('hex');
  await rm(path, { force: true });
  finish({ bytes, checksum, write_ms: writeMs, fsync_ms: fsyncMs, read_ms: readMs, compute_ms: writeMs + readMs });
}

async function runNet() {
  const started = performance.now();
  const response = await fetch(NET_URL);
  if (!response.ok) throw new Error(`Download failed: ${response.status}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  const downloadMs = performance.now() - started;
  const checksum = createHash('sha256').update(buffer).digest('hex');
  if (checksum !== NET_SHA256) throw new Error(`Unexpected download checksum ${checksum}`);
  finish({ url: NET_URL, bytes: buffer.length, checksum, download_ms: downloadMs, compute_ms: downloadMs });
}

async function runInstall() {
  const prefix = join(tmpdir(), `benchpress-install-${token}`);
  const tarball = join(here, 'install-package.tgz');
  const npmCli = join(here, 'node_modules/npm/bin/npm-cli.js');
  const node = join(here, 'node');
  await mkdir(prefix, { recursive: true });
  const started = performance.now();
  await new Promise((resolve, reject) => {
    const child = spawn(node, [npmCli, 'install', tarball, '--prefix', prefix, '--no-fund', '--no-audit'], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, npm_config_update_notifier: 'false' },
    });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code) reject(new Error(`npm install exited ${code}: ${stderr.slice(-800)}`));
      else resolve(undefined);
    });
  });
  const installMs = performance.now() - started;
  const mod = await import(pathToFileURL(join(prefix, 'node_modules/benchpress-install-fixture/index.js')).href);
  if (mod.marker !== 'benchpress-install-ok' || mod.answer() !== 42) {
    throw new Error('Installed package did not export the expected marker');
  }
  const checksum = createHash('sha256').update(`${mod.marker}:${mod.answer()}\n`).digest('hex');
  await rm(prefix, { recursive: true, force: true });
  finish({ checksum, install_ms: installMs, compute_ms: installMs });
}

const passes = { one: 1, ten: 10 }[workload];
try {
  if (workload === 'token') finish();
  else if (passes) await runPasses(passes);
  else if (workload === 'cores') await runCores();
  else if (workload === 'disk') await runDisk();
  else if (workload === 'net') await runNet();
  else if (workload === 'install') await runInstall();
  else throw new Error(`Invalid workload ${workload}`);
} catch (error) {
  console.error(error);
  process.exit(1);
}
