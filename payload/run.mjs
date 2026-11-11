import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';

const [workload, token] = process.argv.slice(2);
const passes = { one: 1, ten: 10 }[workload];
if (!token || (workload !== 'token' && !passes)) throw new Error('Invalid workload or token');

const result = { workload, token, node: process.versions.node };
if (passes) {
  const readStarted = performance.now();
  const fixture = readFileSync(new URL('./fixture.ndjson', import.meta.url));
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
  Object.assign(result, {
    bytes: fixture.length, records, checksum: hash.digest('hex'),
    read_ms: readFinished - readStarted, compute_ms: performance.now() - readFinished,
  });
}
// Keep metadata out of the timed command. The runner collects it afterward.
console.log('BENCHPRESS_RESULT ' + JSON.stringify(result));
