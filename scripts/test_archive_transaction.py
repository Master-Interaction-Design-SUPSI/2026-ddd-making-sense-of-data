import copy
import hashlib
import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import archive_transaction
import build_site
from archive_state import ArchiveSnapshot, blob_sha, encode_archive, seed_v1, unpack_snapshot
from group_registry import Group, load_registry
from publication_state import RecoveryError
import test_publication_state as fixtures


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bootstrap()
        self.registry = load_registry(self.fixture.root / 'groups.json')
        self.snapshot = self.fixture.workspace / 'archive'
        self.snapshot.mkdir()
        self.seed_manifest = (self.fixture.live / 'publication.json').read_bytes()
        groups = seed_v1(self.seed_manifest, self.registry,
                        lambda slug, name: (self.fixture.live / slug / name).read_bytes())
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))
        for slug in groups:
            shutil.copytree(self.fixture.live / slug, self.snapshot / slug)
        self.previous = ArchiveSnapshot(self.snapshot, 'a' * 40)
        self.candidate = self.fixture.workspace / 'candidate'

    def prepare(self, registry=None, unchanged=False):
        return archive_transaction.prepare_archive(registry or self.registry, self.previous, self.candidate,
            client_factory=self.fixture.api.client, allow_unchanged=unchanged)

    def test_valid_update_and_failed_source_retain_accepted_bytes_without_pages(self):
        self.fixture.update('alpha', b'new alpha')
        self.fixture.update('beta', visual=b'broken SVG')
        with patch.object(build_site, 'PublishedSite', side_effect=AssertionError('no Pages authority')):
            report = self.prepare()
            candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
            archive_transaction.admit_candidate(candidate, self.registry, self.previous)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['updated', 'retained'])
        self.assertEqual(candidate.read_file('alpha', 'text.md'), b'new alpha')
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        self.assertEqual(report['transport']['rest_requests'], 8)
        self.assertTrue(candidate.groups['alpha']['snapshot']['tree'])
        self.assertEqual(candidate.groups['alpha']['snapshot']['contract_version'], 2)

    def test_frozen_hidden_and_removed_groups_do_not_read_source_and_preserve_bytes(self):
        registry = [replace(self.registry[0], enabled=False, state='frozen'),
                    replace(self.registry[1], enabled=False, state='hidden')]
        with patch.object(archive_transaction, 'GitHubClient', side_effect=AssertionError('no source read')):
            report = archive_transaction.prepare_archive(registry, self.previous, self.candidate,
                            client_factory=lambda: self.fail('frozen/hidden source accessed'))
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        archive_transaction.admit_candidate(candidate, registry, self.previous)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['frozen', 'hidden'])
        self.assertEqual(set(candidate.served()), {'alpha'})
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        shutil.rmtree(self.candidate)
        report = self.prepare([registry[0]])
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        self.assertEqual(candidate.groups['beta']['state'], 'hidden')
        self.assertEqual(report['removed_groups'][0]['slug'], 'beta')

    def test_unchanged_current_policy_avoids_raw_downloads_and_identical_manifest(self):
        self.prepare()
        self.previous = ArchiveSnapshot(self.candidate, 'b' * 40)
        second = self.fixture.workspace / 'second'
        report = archive_transaction.prepare_archive(self.registry, self.previous, second,
            client_factory=self.fixture.api.client, allow_unchanged=True)
        self.assertEqual(report['transport']['raw_requests'], 0)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['unchanged', 'unchanged'])
        self.assertEqual((second / 'publication.json').read_bytes(), self.previous.payload)

    def test_writer_checks_identity_and_hidden_snapshots_beyond_transfer_integrity(self):
        registry = [replace(self.registry[0], enabled=False, state='hidden'), self.registry[1]]
        self.prepare(registry)
        groups = json.loads((self.candidate / 'publication.json').read_bytes())['groups']
        groups['alpha']['snapshot']['commit'] = 'd' * 40
        (self.candidate / 'publication.json').write_bytes(encode_archive(groups))
        candidate = ArchiveSnapshot(self.candidate, 'b' * 40)
        with self.assertRaisesRegex(RecoveryError, 'hidden'):
            archive_transaction.admit_candidate(candidate, registry, self.previous)
        groups['alpha']['snapshot'] = copy.deepcopy(self.previous.groups['alpha']['snapshot'])
        groups['beta']['repository'] = 'attacker/project'
        (self.candidate / 'publication.json').write_bytes(encode_archive(groups))
        candidate = ArchiveSnapshot(self.candidate, 'b' * 40)
        with self.assertRaisesRegex(RecoveryError, 'identity'):
            archive_transaction.admit_candidate(candidate, registry, self.previous)

    def test_writer_seed_requires_exact_verified_v1_authority(self):
        archive_transaction.admit_candidate(self.previous, self.registry, seed_manifest=self.seed_manifest)
        with self.assertRaisesRegex(RecoveryError, 'seed authority'):
            archive_transaction.admit_candidate(self.previous, self.registry)
        changed = json.loads(self.seed_manifest)
        changed['groups']['alpha']['commit'] = 'd' * 40
        with self.assertRaisesRegex(RecoveryError, 'seed candidate'):
            archive_transaction.admit_candidate(self.previous, self.registry, seed_manifest=json.dumps(changed).encode())

        tampered_folder = self.fixture.workspace / 'tampered-seed'
        shutil.copytree(self.snapshot, tampered_folder)
        content = b'changed after seed admission'
        (tampered_folder / 'alpha/text.md').write_bytes(content)
        groups = copy.deepcopy(self.previous.groups)
        groups['alpha']['snapshot']['files']['text.md'] = {
            'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'blob_sha': blob_sha(content)}
        (tampered_folder / 'publication.json').write_bytes(encode_archive(groups))
        tampered = ArchiveSnapshot(tampered_folder, 'b' * 40)
        with self.assertRaisesRegex(RecoveryError, 'seed bytes'):
            archive_transaction.admit_candidate(tampered, self.registry, seed_manifest=self.seed_manifest)

    def test_projected_whole_site_failure_never_installs_candidate(self):
        with patch.object(build_site, 'MAX_SITE_BYTES', 1):
            with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.candidate.exists())
        self.assertTrue(self.snapshot.exists())

    def test_artifact_is_deterministic_and_round_trips_exact_nested_bytes(self):
        name = 'media/' + '/'.join(['abcdefghij' * 3] * 10) + '/detail.svg'
        content = (self.snapshot / 'alpha/visual.svg').read_bytes()
        target = self.snapshot / 'alpha' / name
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        groups = copy.deepcopy(self.previous.groups)
        groups['alpha']['snapshot']['files'][name] = {'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'blob_sha': blob_sha(content)}
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))
        self.previous = ArchiveSnapshot(self.snapshot, self.previous.commit)
        one, two = self.fixture.workspace / 'one.tar', self.fixture.workspace / 'two.tar'
        self.assertEqual(archive_transaction.pack_snapshot(self.snapshot, one),
                         archive_transaction.pack_snapshot(self.snapshot, two))
        target = self.fixture.workspace / 'unpacked'
        unpack_snapshot(one, target, hashlib.sha256(one.read_bytes()).hexdigest())
        self.assertEqual(ArchiveSnapshot(target, 'a' * 40).payload, self.previous.payload)
        for slug, entry in self.previous.groups.items():
            for name in entry['snapshot']['files']:
                self.assertEqual((target / slug / name).read_bytes(), self.previous.read_file(slug, name))


if __name__ == '__main__':
    unittest.main()
