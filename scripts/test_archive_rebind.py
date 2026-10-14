import copy
import hashlib
import io
import json
import shutil
import unittest
from dataclasses import replace
from contextlib import redirect_stdout

import archive_rebind
import build_site
from archive_state import ArchiveSnapshot, blob_sha, encode_archive, reconcile, seed_v1
from group_registry import Group, load_registry
from publication_state import RecoveryError
import test_publication_state as fixtures


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bootstrap()
        self.registry = load_registry(self.fixture.root / 'groups.json')
        folder = self.fixture.workspace / 'archive-parent'
        folder.mkdir()
        payload = (self.fixture.live / 'publication.json').read_bytes()
        groups = seed_v1(payload, self.registry,
                         lambda slug, name: (self.fixture.live / slug / name).read_bytes())
        (folder / 'publication.json').write_bytes(encode_archive(groups))
        for group in groups:
            if groups[group]['snapshot']:
                shutil.copytree(self.fixture.live / group, folder / group)
        self.previous = ArchiveSnapshot(folder, 'a' * 40)
        self.destination = self.fixture.workspace / 'rebound'
        self.target_registry = [
            replace(self.registry[0], repository='new-owner/new-alpha', branch='release/content'),
            self.registry[1],
        ]

    def prepare(self, registry=None, expected_repository='course/alpha', expected_branch='main'):
        return archive_rebind.prepare_rebind(
            self.previous, registry or self.target_registry, 'alpha', expected_repository,
            expected_branch, self.destination)

    def test_rebind_retires_old_identity_bytes_and_next_reconcile_accepts_new_registry(self):
        # The accepted parent is the migration source of truth. Simulate both
        # the published copy and repository API being unavailable.
        shutil.rmtree(self.fixture.live)
        self.fixture.api.routes.clear()
        candidate, spec = self.prepare()
        self.assertEqual(spec['expected_parent'], self.previous.commit)
        self.assertEqual(spec['old_repository'], 'course/alpha')
        self.assertEqual(spec['old_branch'], 'main')
        self.assertEqual(spec['new_repository'], 'new-owner/new-alpha')
        self.assertEqual(spec['new_branch'], 'release/content')
        self.assertLessEqual(len(spec['retired_slug']), 63)

        active = candidate.groups['alpha']
        retired = candidate.groups[spec['retired_slug']]
        self.assertEqual((active['repository'], active['branch']),
                         ('new-owner/new-alpha', 'release/content'))
        self.assertIsNone(active['snapshot'])
        self.assertEqual(retired['state'], 'hidden')
        self.assertEqual((retired['repository'], retired['branch']), ('course/alpha', 'main'))
        self.assertEqual(retired['snapshot'], self.previous.groups['alpha']['snapshot'])
        for name in retired['snapshot']['files']:
            self.assertEqual(candidate.read_file(spec['retired_slug'], name),
                             self.previous.read_file('alpha', name))
        self.assertEqual(candidate.groups['beta'], self.previous.groups['beta'])

        archive_rebind.admit_rebind(candidate, self.target_registry, self.previous, spec)
        next_groups = reconcile(candidate.groups, self.target_registry)
        self.assertEqual(next_groups['alpha']['repository'], 'new-owner/new-alpha')
        self.assertIsNone(next_groups['alpha']['snapshot'])
        self.assertEqual(next_groups[spec['retired_slug']]['state'], 'hidden')
        self.assertEqual(next_groups[spec['retired_slug']]['snapshot'], retired['snapshot'])

    def test_old_identity_parent_and_target_registry_are_bound(self):
        with self.assertRaisesRegex(RecoveryError, 'old identity'):
            self.prepare(expected_repository='course/other')
        with self.assertRaisesRegex(RecoveryError, 'old identity'):
            self.prepare(expected_branch='draft')

        candidate, spec = self.prepare()
        changed = copy.deepcopy(spec)
        changed['expected_parent'] = 'b' * 40
        with self.assertRaisesRegex(RecoveryError, 'wrong archive parent'):
            archive_rebind.admit_rebind(candidate, self.target_registry, self.previous, changed)

        wrong_target = [replace(self.target_registry[0], repository='attacker/project'),
                        self.target_registry[1]]
        with self.assertRaisesRegex(RecoveryError, 'trusted registry'):
            archive_rebind.admit_rebind(candidate, wrong_target, self.previous, spec)

        wrong_parent = ArchiveSnapshot(candidate.folder, 'b' * 40)
        with self.assertRaisesRegex(RecoveryError, 'wrong archive parent'):
            archive_rebind.admit_rebind(wrong_parent, self.target_registry, self.previous, spec)

    def test_identity_must_change_and_retired_slug_collision_is_rejected(self):
        with self.assertRaisesRegex(RecoveryError, 'does not differ'):
            self.prepare(registry=self.registry)
        with self.assertRaisesRegex(RecoveryError, 'valid group slug'):
            archive_rebind.prepare_rebind(self.previous, self.target_registry, [], 'course/alpha',
                                          'main', self.destination)

        retired_slug = archive_rebind._retired_slug('alpha', 'course/alpha', 'main')
        collision = [*self.target_registry,
                     Group(retired_slug, 'course/retired-alpha', 'main', False)]
        with self.assertRaisesRegex(RecoveryError, 'already in use'):
            self.prepare(registry=collision)

    def test_retired_content_cannot_be_replaced_even_with_a_valid_candidate_manifest(self):
        candidate, spec = self.prepare()
        changed_folder = self.fixture.workspace / 'tampered'
        shutil.copytree(candidate.folder, changed_folder)
        text_path = changed_folder / spec['retired_slug'] / 'text.md'
        content = b'Changed after migration preparation\n'
        text_path.write_bytes(content)
        groups = copy.deepcopy(candidate.groups)
        groups[spec['retired_slug']]['snapshot']['files']['text.md'] = {
            'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
            'blob_sha': blob_sha(content),
        }
        (changed_folder / 'publication.json').write_bytes(encode_archive(groups))
        tampered = ArchiveSnapshot(changed_folder, self.previous.commit)
        with self.assertRaisesRegex(RecoveryError, 'differs from the independently derived migration'):
            archive_rebind.admit_rebind(tampered, self.target_registry, self.previous, spec)

    def test_rebind_refuses_unrelated_identity_changes_but_allows_trusted_state_update(self):
        changed_peer = [self.target_registry[0],
                        replace(self.registry[1], repository='other-owner/beta')]
        with self.assertRaisesRegex(RecoveryError, 'identity/settings'):
            self.prepare(registry=changed_peer)

        changed_settings = [self.target_registry[0],
                            replace(self.registry[1], require_visual=False)]
        with self.assertRaisesRegex(RecoveryError, 'identity/settings'):
            self.prepare(registry=changed_settings)

        hidden_peer = [self.target_registry[0],
                       replace(self.registry[1], enabled=False, state='hidden')]
        candidate, spec = self.prepare(registry=hidden_peer)
        self.assertEqual(candidate.groups['beta']['state'], 'hidden')
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        archive_rebind.admit_rebind(candidate, hidden_peer, self.previous, spec)

    def test_cli_prepares_new_output_and_validate_reloads_parent_registry_and_spec(self):
        registry_path = self.fixture.root / 'rebind-registry.json'
        registry_path.write_text(json.dumps({'groups': [
            {'slug': 'alpha', 'repository': 'new-owner/new-alpha', 'branch': 'release/content',
             'state': 'live'},
            {'slug': 'beta', 'repository': 'course/beta', 'branch': 'main', 'state': 'live'},
        ]}))
        output = self.fixture.workspace / 'cli-result'
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            archive_rebind.main([
                'prepare', '--archive-snapshot', str(self.previous.folder),
                '--archive-commit', self.previous.commit, '--registry', str(registry_path),
                '--slug', 'alpha', '--old-repository', 'course/alpha', '--old-branch', 'main',
                '--out', str(output),
            ])
        spec = json.loads(stdout.getvalue())
        self.assertEqual(json.loads((output / 'rebind.json').read_text()), spec)
        self.assertTrue((output / 'candidate/publication.json').is_file())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            archive_rebind.main([
                'validate', '--archive-snapshot', str(output / 'candidate'),
                '--parent-snapshot', str(self.previous.folder), '--archive-commit', self.previous.commit,
                '--registry', str(registry_path), '--spec', str(output / 'rebind.json'),
            ])
        result = json.loads(stdout.getvalue())
        self.assertTrue(result['valid'])
        self.assertEqual(result['spec'], spec)

        with self.assertRaisesRegex(RecoveryError, 'must be new'):
            archive_rebind.main([
                'prepare', '--archive-snapshot', str(self.previous.folder),
                '--archive-commit', self.previous.commit, '--registry', str(registry_path),
                '--slug', 'alpha', '--old-repository', 'course/alpha', '--old-branch', 'main',
                '--out', str(output),
            ])


if __name__ == '__main__':
    unittest.main()
