import copy
import json
import shutil
import unittest
from unittest.mock import Mock, patch

import archive_release
import build_site
from archive_state import ArchiveSnapshot, encode_archive, seed_v1
from deployment_records import DeploymentRecords, ENVIRONMENT, TASK
from group_registry import load_registry
from publication_state import RecoveryError
from publish_site import served_release, validate_release
import test_publication_state as fixtures


class ReleasePairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bootstrap()
        groups = seed_v1((self.fixture.live / 'publication.json').read_bytes(),
            load_registry(self.fixture.root / 'groups.json'), lambda slug, name: (self.fixture.live / slug / name).read_bytes())
        folder = self.fixture.workspace / 'archive'
        folder.mkdir()
        for slug in groups:
            shutil.copytree(self.fixture.live / slug, folder / slug)
        (folder / 'publication.json').write_bytes(encode_archive(groups))
        self.archive = ArchiveSnapshot(folder, 'c' * 40)
        self.release = archive_release.make_release('a' * 40, 'b' * 40, self.archive, '50', '1')

    def bytes(self):
        return {p.relative_to(self.fixture.output).as_posix(): p.read_bytes()
                for p in self.fixture.output.rglob('*') if p.is_file()}

    def test_exact_pair_rebuilds_all_site_bytes_without_artifact_or_source(self):
        expected = self.bytes()
        shutil.rmtree(self.fixture.output)
        with patch.object(build_site, 'PublishedSite', side_effect=AssertionError('no Pages recovery')), \
                patch.object(build_site, 'import_groups', side_effect=AssertionError('no source reads')):
            archive_release.reconstruct(self.release, self.archive, 'a' * 40, 'b' * 40)
        self.assertEqual(self.bytes(), expected)

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
        served_release('https://site.test/course/', self.release, attempts=1)
        path = self.fixture.live / 'beta/text.md'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaisesRegex(RecoveryError, 'content probe'):
            served_release('https://site.test/course/', self.release, attempts=1)

    def test_release_digest_excludes_release_identity_and_same_input_is_deterministic(self):
        second = archive_release.make_release('a' * 40, 'b' * 40, self.archive, '51', '2')
        self.assertEqual(second['site_sha256'], self.release['site_sha256'])
        self.assertEqual(second['archive_manifest_sha256'], self.release['archive_manifest_sha256'])
        self.assertNotEqual(second['run_id'], self.release['run_id'])
        unchanged = archive_release.probes_for(self.archive.served(), self.archive.served())
        self.assertEqual(unchanged, [])

    def test_pending_pair_retry_precedes_import_and_code_change_explicitly_supersedes(self):
        attempted = {'id': 1, 'payload': self.release}
        self.assertEqual(archive_release.publication_strategy(attempted, None, 'a' * 40)['mode'], 'verify_then_retry')
        verified = copy.deepcopy(attempted)
        self.assertEqual(archive_release.publication_strategy(attempted, verified, 'a' * 40)['mode'], 'refresh')
        self.assertEqual(archive_release.publication_strategy(attempted, None, 'd' * 40), {'mode': 'refresh', 'supersedes': 1})

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
