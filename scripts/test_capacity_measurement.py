"""Focused smoke assertions for the manual local archive capacity harness."""

import tempfile
import unittest
import json
from pathlib import Path

from measure_archive_capacity import SCENARIOS, TEXT, measure


class CapacityMeasurementTests(unittest.TestCase):
    def test_six_cycle_smoke_exercises_reuse_idle_projection_and_history(self):
        with tempfile.TemporaryDirectory(prefix='capacity-test-') as temporary:
            destination = Path(temporary) / 'capacity.json'
            report = measure('smoke', output=destination, scratch=temporary)
            self.assertEqual(json.loads(destination.read_text()), report)

        self.assertEqual(report['measurement_kind'], 'local_and_simulated_only')
        self.assertIn('not wire bytes', report['wire_metrics_warning'])
        self.assertEqual([cycle['scenario'] for cycle in report['cycles']], list(SCENARIOS))
        cold, idle_a, text_edit, full_a, idle_b, full_b = report['cycles']

        self.assertEqual(cold['raw_requests'], 14)
        self.assertGreater(cold['raw_body_bytes'], 0)
        self.assertGreater(cold['site_bytes'], cold['input_bytes'])
        self.assertGreater(cold['candidate_tar_bytes'], cold['input_bytes'])
        self.assertEqual(len(cold['per_group']), 2)
        self.assertTrue(all(group['elapsed_seconds'] is not None for group in cold['per_group']))

        for idle in (idle_a, idle_b):
            self.assertTrue(idle['idle'])
            self.assertEqual(idle['raw_requests'], 0)
            self.assertEqual(idle['raw_body_bytes'], 0)
            self.assertEqual(idle['verified_reuse_bytes'], 0)
            self.assertIsNone(idle['site_bytes'])
            self.assertNotIn('archive_commit', idle)

        self.assertEqual(text_edit['raw_requests'], 2)
        self.assertEqual(text_edit['raw_body_bytes'], 2 * len(TEXT.replace(b'fixture', b'fixture text edit')))
        self.assertGreater(text_edit['verified_reuse_bytes'], text_edit['raw_body_bytes'])
        self.assertGreater(full_a['raw_body_bytes'], text_edit['raw_body_bytes'])
        self.assertGreater(full_a['reachable_history_object_bytes'], text_edit['reachable_history_object_bytes'])
        self.assertGreater(full_b['reachable_history_object_bytes'], full_a['reachable_history_object_bytes'])
        self.assertGreater(full_b['local_remote_object_disk_growth_bytes'], 0)
        self.assertGreater(full_b['fresh_shallow_fetch_git_objects_bytes_proxy_not_wire'], 0)

    def test_report_path_must_be_new(self):
        with tempfile.TemporaryDirectory(prefix='capacity-report-test-') as temporary:
            path = Path(temporary) / 'report.json'
            path.write_text('keep')
            with self.assertRaises(FileExistsError):
                measure('smoke', output=path, scratch=temporary)
            self.assertEqual(path.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
