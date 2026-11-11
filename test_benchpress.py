import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import benchpress


class WorkloadTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'requires Node.js')
    def test_payload_validates_and_rejects_a_bad_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in ('fixture.mjs', 'run.mjs'):
                shutil.copyfile(benchpress.PAYLOAD / path, Path(tmp) / path)
            subprocess.run(['node', 'fixture.mjs'], cwd=tmp, check=True)
            # The local Node can differ. The deployed adapter enforces the pin.
            node = subprocess.check_output(['node', '-p', 'process.versions.node'], text=True).strip()
            with patch.object(benchpress, 'NODE', node):
                for workload in benchpress.WORKLOADS:
                    output = subprocess.check_output(['node', 'run.mjs', workload, 'nonce'], cwd=tmp, text=True)
                    result = benchpress.verify(output, workload, 'nonce')
                    with self.assertRaisesRegex(ValueError, 'token'):
                        benchpress.verify(output, workload, 'wrong-nonce')
                    if workload != 'token':
                        result['checksum'] = '0' * 64
                        with self.assertRaisesRegex(ValueError, 'checksum'):
                            benchpress.verify('BENCHPRESS_RESULT ' + json.dumps(result), workload, 'nonce')

    def test_timeout_removes_fresh_box_and_skips_warm(self):
        class Box:
            version = 'test'
            id = 'fake-box'

            def launch(self, timeout):
                time.sleep(1)

            def close(self):
                return 'removed'

        with patch.object(benchpress, 'Sandbox', return_value=Box()), patch.object(benchpress, 'DEADLINE', .03):
            row = benchpress.trial('modal', {}, 'token', 0)
        self.assertEqual(row['cold']['status'], 'timeout')
        self.assertEqual(row['cleanup'], 'removed')
        self.assertEqual(row['warm']['status'], 'not_run')

    def test_schedule_and_bootstrap(self):
        tasks = list(benchpress.schedule(benchpress.PROVIDERS, seed=7256))
        self.assertEqual(len(tasks), 360)
        self.assertEqual(len(tasks), len(set(tasks)))
        self.assertNotEqual(tasks[:12], [(1, work, provider) for _, work, provider in tasks[12:24]])
        pairs = {index: (3 * (index + 1), index + 1) for index in range(30)}
        self.assertEqual(benchpress.interval(pairs), (3.0, 3.0))


if __name__ == '__main__':
    unittest.main()
