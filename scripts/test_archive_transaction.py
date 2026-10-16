import copy
import hashlib
import json
import shutil
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.error import URLError

import archive_transaction
import build_site
from archive_state import ArchiveSnapshot, empty_archive, encode_archive, file_record, unpack_snapshot
from release import PublicationError
from test_fixtures import Course


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Course(self)
        self.registry = self.fixture.groups()
        self.previous = self.fixture.first_archive()
        self.snapshot = self.previous.folder
        self.candidate = self.fixture.workspace / 'candidate'

    def prepare(self, registry=None, unchanged=False, idle=False, destination=None):
        destination = destination or self.candidate
        return archive_transaction.prepare_archive(registry or self.registry, self.previous, destination,
            client_factory=self.fixture.api.client, allow_unchanged=unchanged, allow_idle=idle)

    def test_valid_update_and_failed_source_retain_accepted_bytes_without_pages(self):
        self.fixture.update('alpha', b'new alpha')
        self.fixture.update('beta', visual=b'broken SVG')
        with patch('release.build_opener', side_effect=AssertionError('no Pages reads')):
            report = self.prepare()
            candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
            archive_transaction.admit_candidate(candidate, self.registry, self.previous)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['updated', 'retained'])
        self.assertEqual(candidate.read_file('alpha', 'text.md'), b'new alpha')
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        self.assertEqual(report['transport']['rest_requests'], 8)
        self.assertTrue(candidate.groups['alpha']['snapshot']['tree'])
        self.assertEqual(candidate.groups['alpha']['snapshot']['contract_version'], 2)

    def test_rejected_commit_is_recorded_as_a_problem_while_the_old_snapshot_stays_online(self):
        online = self.previous.groups['alpha']['snapshot']
        rejected = self.fixture.update('alpha', b'new alpha', visual=b'broken SVG')
        report = self.prepare(unchanged=True)
        candidate = ArchiveSnapshot(self.candidate)
        archive_transaction.admit_candidate(candidate, self.registry, self.previous)
        problem = candidate.groups['alpha']['problem']
        self.assertEqual(problem['commit'], rejected)
        self.assertIn('visual.svg', problem['message'])
        self.assertLessEqual(len(problem['message']), 300)
        self.assertEqual(candidate.groups['alpha']['snapshot'], online)
        self.assertEqual(report['groups'][0]['publication_status'], 'retained')

        # The same rejected commit is not downloaded again and does not block an idle run.
        self.previous = candidate
        self.fixture.api.calls.clear()
        report = self.prepare(unchanged=True, idle=True, destination=self.fixture.workspace / 'known-problem')
        self.assertTrue(report['idle'])
        self.assertFalse(any('/git/trees/' in call or 'raw' in call for call in self.fixture.api.calls
                             if '/course/alpha' in call))
        self.assertEqual(report['groups'][0]['status'], 'rejected')
        self.assertEqual(report['groups'][0]['error'], problem['message'])

        # A later accepted commit clears the problem.
        fixed = self.fixture.update('alpha', b'fixed alpha')
        destination = self.fixture.workspace / 'fixed'
        self.prepare(unchanged=True, idle=True, destination=destination)
        accepted = ArchiveSnapshot(destination)
        self.assertIsNone(accepted.groups['alpha']['problem'])
        self.assertEqual(accepted.groups['alpha']['snapshot']['commit'], fixed)

    def test_first_commit_rejected_records_only_the_problem(self):
        self.fixture.api.add('course/gamma', {'text.md': b'text', 'visual.svg': b'not an SVG'})
        registry = self.registry + [replace(self.registry[0], slug='gamma', repository='course/gamma')]
        self.prepare(registry)
        candidate = ArchiveSnapshot(self.candidate)
        self.assertIsNone(candidate.groups['gamma']['snapshot'])
        self.assertEqual(candidate.groups['gamma']['problem']['commit'], hashlib.sha1(b'course/gamma').hexdigest())

    def test_transport_failure_is_not_a_problem(self):
        self.fixture.update('alpha', b'new alpha')
        self.fixture.api.routes['/repos/course/alpha'] = URLError('temporary outage')
        with patch('github_content.time.sleep'):
            report = self.prepare()
        candidate = ArchiveSnapshot(self.candidate)
        self.assertEqual(report['groups'][0]['error_code'], 'network_error')
        self.assertIsNone(candidate.groups['alpha']['problem'])

    def test_snapshot_records_its_commit_date(self):
        self.assertEqual(self.previous.groups['alpha']['snapshot']['committed_at'], '2026-10-14T10:00:00Z')

    def test_frozen_hidden_and_removed_groups_do_not_read_source_and_preserve_bytes(self):
        registry = [replace(self.registry[0], state='frozen'), replace(self.registry[1], state='hidden')]
        with patch.object(archive_transaction, 'GitHubClient', side_effect=AssertionError('no source read')):
            report = archive_transaction.prepare_archive(registry, self.previous, self.candidate,
                            client_factory=lambda: self.fail('frozen/hidden source accessed'))
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        archive_transaction.admit_candidate(candidate, registry, self.previous)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['frozen', 'hidden'])
        self.assertEqual(set(candidate.served()), {'alpha'})
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        shutil.rmtree(self.candidate)
        self.prepare([registry[0]])
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        archive_transaction.admit_candidate(candidate, [registry[0]], self.previous)
        self.assertEqual(set(candidate.groups), {'alpha'})
        self.assertFalse((self.candidate / 'beta').exists())

    def test_changed_repository_starts_the_snapshot_over(self):
        moved = [replace(self.registry[0], repository='course/alpha-moved'), self.registry[1]]
        report = self.prepare(moved)
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        archive_transaction.admit_candidate(candidate, moved, self.previous)
        self.assertIsNone(candidate.groups['alpha']['snapshot'])
        self.assertEqual(report['groups'][0]['publication_status'], 'skipped')
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])
        self.fixture.api.add('course/alpha-moved', {'text.md': b'Moved alpha', 'visual.svg': self.fixture.bytes_at(
            self.snapshot / 'alpha')['visual.svg']})
        shutil.rmtree(self.candidate)
        report = self.prepare(moved)
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        archive_transaction.admit_candidate(candidate, moved, self.previous)
        self.assertEqual(candidate.read_file('alpha', 'text.md'), b'Moved alpha')
        self.assertEqual(report['groups'][0]['publication_status'], 'updated')

    def test_unchanged_current_policy_avoids_raw_downloads_and_identical_manifest(self):
        second = self.fixture.workspace / 'second'
        report = archive_transaction.prepare_archive(self.registry, self.previous, second,
            client_factory=self.fixture.api.client, allow_unchanged=True)
        self.assertEqual(report['transport']['raw_requests'], 0)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['unchanged', 'unchanged'])
        self.assertEqual((second / 'publication.json').read_bytes(), self.previous.payload)

    def test_idle_checks_source_metadata_without_building_or_reading_archive_blobs(self):
        destination = self.fixture.workspace / 'idle'
        with patch.object(archive_transaction, 'projected_site', side_effect=AssertionError('idle must not project')), \
                patch.object(build_site, 'build_archive', side_effect=AssertionError('idle must not build')):
            report = self.prepare(unchanged=True, idle=True, destination=destination)
        self.assertTrue(report['idle'])
        self.assertFalse(destination.exists())
        self.assertEqual([row['publication_status'] for row in report['groups']], ['unchanged', 'unchanged'])
        self.assertEqual([row['selected_source']['repository'] for row in report['groups']],
                         ['course/alpha', 'course/beta'])
        self.assertEqual(report['transport']['raw_requests'], 0)
        self.assertEqual(report['transport']['archive_read_bytes'], 0)
        self.assertGreater(report['transport']['rest_requests'], 0)

    def test_state_change_disables_idle_and_frozen_snapshot_is_retained(self):
        destination = self.fixture.workspace / 'state-change'
        registry = [replace(self.registry[0], state='frozen'), self.registry[1]]
        report = self.prepare(registry, unchanged=True, idle=True, destination=destination)
        candidate = ArchiveSnapshot(destination, self.previous.commit)
        self.assertFalse(report['idle'])
        self.assertEqual(report['groups'][0]['publication_status'], 'frozen')
        self.assertEqual(candidate.groups['alpha']['snapshot'], self.previous.groups['alpha']['snapshot'])

    def test_failed_live_source_check_disables_idle_and_retains_accepted_bytes(self):
        self.fixture.update('alpha', b'bad live update', b'broken SVG')
        destination = self.fixture.workspace / 'failed-source'
        report = self.prepare(unchanged=True, idle=True, destination=destination)
        candidate = ArchiveSnapshot(destination, self.previous.commit)
        self.assertFalse(report['idle'])
        alpha = next(row for row in report['groups'] if row['slug'] == 'alpha')
        self.assertEqual(alpha['publication_status'], 'retained')
        for name in self.previous.groups['alpha']['snapshot']['files']:
            self.assertEqual(candidate.read_file('alpha', name), self.previous.read_file('alpha', name))

    def test_archive_reuse_timeout_retains_that_group_while_another_update_succeeds(self):
        self.fixture.update('alpha', b'New alpha text')
        self.fixture.update('beta', b'New beta text')
        original = archive_transaction.ArchiveBase.reuse

        def expired_alpha(base, slug, name, node):
            if slug == 'alpha':
                base.deadline = 0
            return original(base, slug, name, node)

        destination = self.fixture.workspace / 'reuse-timeout'
        with patch.object(archive_transaction.ArchiveBase, 'reuse', expired_alpha):
            report = self.prepare(unchanged=True, idle=True, destination=destination)
        candidate = ArchiveSnapshot(destination, self.previous.commit)
        alpha, beta = report['groups']
        self.assertEqual(alpha['error_code'], 'timeout')
        self.assertEqual(alpha['publication_status'], 'retained')
        self.assertEqual(beta['publication_status'], 'updated')
        self.assertEqual(candidate.groups['alpha']['snapshot'], self.previous.groups['alpha']['snapshot'])
        self.assertEqual(candidate.read_file('beta', 'text.md'), b'New beta text')

    def test_low_api_headroom_retains_all_groups_without_spending_remaining_quota(self):
        self.fixture.update('alpha', b'New alpha text')
        self.fixture.update('beta', b'New beta text')
        self.fixture.api.calls.clear()
        original = self.fixture.api.open

        def limited(request, timeout):
            response = original(request, timeout)
            response.headers = {'X-RateLimit-Remaining': '200'}
            return response

        with patch.object(self.fixture.api, 'open', limited):
            report = self.prepare()
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        self.assertEqual(len(self.fixture.api.calls), 1)
        self.assertEqual(report['transport']['raw_requests'], 0)
        self.assertEqual([row['error_code'] for row in report['groups']], ['rate_limited'] * 2)
        self.assertEqual([row['publication_status'] for row in report['groups']], ['retained'] * 2)
        for slug, entry in self.previous.groups.items():
            self.assertEqual(candidate.groups[slug]['snapshot'], entry['snapshot'])
            for name in entry['snapshot']['files']:
                self.assertEqual(candidate.read_file(slug, name), self.previous.read_file(slug, name))

    def test_expired_run_deadline_retains_later_group_without_another_source_request(self):
        self.fixture.update('alpha', b'New alpha text')
        self.fixture.update('beta', b'New beta text')
        self.fixture.api.calls.clear()
        clock = [0.0]
        clients = []

        def factory():
            if clients:
                clock[0] = 901.0
            client = self.fixture.api.client()
            clients.append(client)
            return client

        with patch('import_groups.time.monotonic', side_effect=lambda: clock[0]):
            report = archive_transaction.prepare_archive(self.registry, self.previous, self.candidate,
                                                         client_factory=factory)
        candidate = ArchiveSnapshot(self.candidate, self.previous.commit)
        alpha, beta = report['groups']
        self.assertEqual(alpha['publication_status'], 'updated')
        self.assertEqual(beta['error_code'], 'timeout')
        self.assertEqual(beta['publication_status'], 'retained')
        self.assertEqual(clients[1].metrics['rest_requests'], 0)
        self.assertEqual(clients[1].metrics['raw_requests'], 0)
        self.assertEqual(candidate.read_file('alpha', 'text.md'), b'New alpha text')
        self.assertEqual(candidate.groups['beta']['snapshot'], self.previous.groups['beta']['snapshot'])

    def test_writer_checks_identity_and_hidden_snapshots_beyond_transfer_integrity(self):
        registry = [replace(self.registry[0], state='hidden'), self.registry[1]]
        self.prepare(registry)
        groups = json.loads((self.candidate / 'publication.json').read_bytes())['groups']
        groups['alpha']['snapshot']['commit'] = 'd' * 40
        (self.candidate / 'publication.json').write_bytes(encode_archive(groups))
        candidate = ArchiveSnapshot(self.candidate, 'b' * 40)
        with self.assertRaisesRegex(PublicationError, 'hidden'):
            archive_transaction.admit_candidate(candidate, registry, self.previous)
        groups['alpha']['snapshot'] = copy.deepcopy(self.previous.groups['alpha']['snapshot'])
        groups['beta']['repository'] = 'attacker/project'
        (self.candidate / 'publication.json').write_bytes(encode_archive(groups))
        candidate = ArchiveSnapshot(self.candidate, 'b' * 40)
        with self.assertRaisesRegex(PublicationError, 'identity'):
            archive_transaction.admit_candidate(candidate, registry, self.previous)

    def test_writer_admits_a_first_archive_only_against_the_registry(self):
        empty = empty_archive(self.fixture.workspace / 'empty-parent')
        archive_transaction.admit_candidate(self.previous, self.registry, empty)
        with self.assertRaisesRegex(PublicationError, 'unapproved archive row'):
            archive_transaction.admit_candidate(self.previous, self.registry[:1], empty)
        hidden = [replace(self.registry[0], state='hidden'), self.registry[1]]
        with self.assertRaisesRegex(PublicationError, 'identity/state'):
            archive_transaction.admit_candidate(self.previous, hidden, empty)
        groups = copy.deepcopy(self.previous.groups)
        groups['alpha']['state'] = 'hidden'
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))
        with self.assertRaisesRegex(PublicationError, 'hidden bytes'):
            archive_transaction.admit_candidate(ArchiveSnapshot(self.snapshot, None), hidden, empty)

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
        groups['alpha']['snapshot']['files'][name] = file_record(content)
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
