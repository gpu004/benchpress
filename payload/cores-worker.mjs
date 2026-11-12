import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { parentPort, workerData } from 'node:worker_threads';

const fixture = readFileSync(workerData.fixturePath);
const totals = new Map();
for (const line of fixture.toString('ascii').trimEnd().split('\n')) {
  const { key, value } = JSON.parse(line);
  totals.set(key, (totals.get(key) ?? 0) + value);
}
const checksum = createHash('sha256')
  .update(JSON.stringify([...totals].sort(([a], [b]) => a - b)) + '\n')
  .digest('hex');
if (!parentPort) throw new Error('cores-worker must run in a worker thread');
parentPort.postMessage({ checksum, bytes: fixture.length, records: fixture.toString('ascii').trimEnd().split('\n').length });
