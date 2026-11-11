import { readFileSync } from 'node:fs';
import { availableParallelism, cpus, totalmem } from 'node:os';

function read(path) {
  try { return readFileSync(path, 'utf8').trim(); } catch { return null; }
}

const cpu = cpus();
const region = process.env.MODAL_REGION ?? process.env.AWS_REGION ?? null;
console.log('BENCHPRESS_MACHINE ' + JSON.stringify({
  os: process.platform, arch: process.arch, cpu_model: cpu[0]?.model ?? null,
  visible_cpus: cpu.length, available_parallelism: availableParallelism(),
  memory_bytes: totalmem(), node: process.versions.node, region,
  cpu_max: read('/sys/fs/cgroup/cpu.max'),
  cpu_quota_us: read('/sys/fs/cgroup/cpu/cpu.cfs_quota_us'),
  cpu_period_us: read('/sys/fs/cgroup/cpu/cpu.cfs_period_us'),
  memory_max: read('/sys/fs/cgroup/memory.max') ?? read('/sys/fs/cgroup/memory/memory.limit_in_bytes'),
}));
