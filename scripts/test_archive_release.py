import copy
import hashlib
import json
import shutil
import unittest
from unittest.mock import Mock, patch

import archive_release
import archive_transaction
from deployment_records import DeploymentRecords, ENVIRONMENT, TASK
from publication_state import RecoveryError
from publish_site import served_release, validate_release
from test_fixtures import URL, Course


class ReleasePairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Course(self)
        self.archive = self.fixture.first_archive(commit='c' * 40)
        self.release = archive_release.make_release('a' * 40, 'b' * 40, self.archive, '50', '1')

    def test_exact_pair_rebuilds_all_site_bytes_without_artifact_or_source(self):
        expected = self.fixture.bytes_at(self.fixture.output)
        shutil.rmtree(self.fixture.output)
        with patch.object(archive_transaction, 'import_groups', side_effect=AssertionError('no source reads')), \
                patch('publication_state.build_opener', side_effect=AssertionError('no Pages reads')):
            archive_release.reconstruct(self.release, self.archive, 'a' * 40, 'b' * 40)
        self.assertEqual(self.fixture.bytes_at(self.fixture.output), expected)

    def test_reconstruction_rejects_other_code_archive_or_changed_trusted_assets(self):
        with self.assertRaises(RecoveryError):
            archive_release.reconstruct(self.release, self.archive, 'd' * 40, 'b' * 40)
        wrong = copy.deepcopy(self.release)
        wrong['archive_commit'] = 'd' * 40
        with self.assertRaises(RecoveryError):
            archive_release.reconstruct(wrong, self.archive, 'a' * 40, 'b' * 40)
        (self.fixture.root / 'index.html').write_text('changed code even though caller supplied old pin')
        with self.assertRaisesRegex(RecoveryError, 'differs'):
            archive_release.reconstruct(self.release, self.archive, 'a' * 40, 'b' * 40)

    def test_changed_text_and_representative_media_are_probed_on_served_site(self):
        self.assertEqual(self.release['probes'], ['alpha/text.md', 'beta/text.md', 'alpha/media/detail.svg'])
        self.fixture.deploy()
        served_release(URL, self.release, attempts=1)
        path = self.fixture.live / 'beta/text.md'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaisesRegex(RecoveryError, 'content probe'):
            served_release(URL, self.release, attempts=1)

    def test_served_verification_has_one_overall_deadline_across_retries(self):
        clock = {'seconds': 0}

        def expired_read(*arguments):
            clock['seconds'] = 181
            raise RecoveryError('stale release')

        with patch('publish_site.time.monotonic', side_effect=lambda: clock['seconds']), \
                patch('publish_site.SiteReader') as reader, patch('publish_site.time.sleep') as sleep:
            reader.return_value.read.side_effect = expired_read
            with self.assertRaisesRegex(RecoveryError, 'stale release'):
                served_release(URL, self.release)
        self.assertEqual(reader.call_count, 1)
        sleep.assert_not_called()

    def test_release_digest_excludes_release_identity_and_same_input_is_deterministic(self):
        second = archive_release.make_release('a' * 40, 'b' * 40, self.archive, '51', '2')
        self.assertEqual(second['site_sha256'], self.release['site_sha256'])
        self.assertEqual(second['archive_manifest_sha256'], self.release['archive_manifest_sha256'])
        self.assertNotEqual(second['run_id'], self.release['run_id'])
        unchanged = archive_release.probes_for(self.archive.served(), self.archive.served())
        self.assertEqual(unchanged, [])

    def test_changed_media_probe_skips_unchanged_earlier_media(self):
        current = self.archive.served()
        previous = copy.deepcopy(current)
        previous['beta']['files']['media/detail.svg']['sha256'] = '0' * 64
        probes = archive_release.probes_for(current, previous)
        self.assertEqual(probes, ['beta/media/detail.svg'])

    def test_previous_served_inventory_requires_verified_release_and_matching_manifest(self):
        previous = self.release | {'archive_commit': 'c' * 40,
                                   'archive_manifest_sha256': hashlib.sha256(self.archive.payload).hexdigest()}
        verified = {'id': 7, 'payload': previous}
        records = Mock()
        records.client.raw_file.return_value = self.archive.payload
        served = archive_release.verified_previous_groups(records, 'course/site', verified)
        self.assertEqual(set(served), set(self.archive.served()))
        for slug in served:
            self.assertEqual(served[slug]['files'], self.archive.served()[slug]['files'])
        records.client.raw_file.assert_called_once_with('course/site', 'c' * 40, 'publication.json',
                                                        archive_release.MAX_MANIFEST_BYTES)

        records.client.raw_file.reset_mock()
        self.assertIsNone(archive_release.verified_previous_groups(records, 'course/site', None))
        records.client.raw_file.assert_not_called()

        records.client.raw_file.return_value = b'{}'
        with self.assertRaisesRegex(RecoveryError, 'manifest differs'):
            archive_release.verified_previous_groups(records, 'course/site', verified)

    def test_pending_pair_retry_precedes_import_and_code_change_explicitly_supersedes(self):
        attempted = {'id': 1, 'payload': self.release}
        self.assertEqual(archive_release.publication_strategy(attempted, None, 'a' * 40)['mode'], 'verify_then_retry')
        verified = copy.deepcopy(attempted)
        self.assertEqual(archive_release.publication_strategy(attempted, verified, 'a' * 40)['mode'], 'refresh')
        self.assertEqual(archive_release.publication_strategy(attempted, None, 'd' * 40), {'mode': 'refresh'})

    def test_schema_rejects_missing_pins_and_unsafe_or_unbounded_probes(self):
        for probes in (['../escape'], ['alpha/text.md', 'alpha/text.md'], [None], [['alpha/text.md']], ['alpha/text.md'] * 17):
            with self.subTest(probes=probes), self.assertRaises(RecoveryError):
                validate_release(self.release | {'probes': probes})
        with self.assertRaises(RecoveryError): validate_release(self.release | {'archive_commit': 'main'})

    def test_verified_history_distinguishes_failed_attempt_from_previous_success(self):
        records = DeploymentRecords('course/site')
        records.client = Mock()
        old, latest = ({'id': n, 'task': TASK, 'environment': ENVIRONMENT, 'payload': self.release} for n in (1, 2))
        records.client.get.side_effect = [json.dumps([latest, old]).encode(),
            json.dumps([{'state': 'in_progress'}]).encode(), json.dumps([{'state': 'success'}]).encode()]
        self.assertEqual(records.latest_verified(), old)
        self.assertEqual(records.client.get.call_count, 3)
        records.client.get.side_effect = [b'[]']
        self.assertIsNone(records.latest_verified())


if __name__ == '__main__':
    unittest.main()
