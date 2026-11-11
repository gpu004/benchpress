import { writeFileSync } from 'node:fs';

const rowCount = 524288;
const width = 64;
const fixture = Buffer.allocUnsafe(rowCount * width);
for (let i = 0; i < rowCount; i++) {
  const key = (i * 73 + 19) % 997;
  const value = (i * 127 + 23) % 10007 - 5000;
  const prefix = `{"key":${key},"value":${value},"pad":"`;
  const row = `${prefix}${'x'.repeat(width - prefix.length - 3)}"}\n`;
  fixture.write(row, i * width, width, 'ascii');
}
writeFileSync(new URL('./fixture.ndjson', import.meta.url), fixture);
