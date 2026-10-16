import copy
import json
import os
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import archive_git
import archive_transaction
import publish as controller
from release import PublicationError
from test_fixtures import SVG, TEXT, URL, Course, entry_slugs
from test_publication_requests import API, issue


class SplitPublisherTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Course(self)
        self.root = self.fixture.workspace
        self.remote = self.root / 'course.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(self.remote)], check=True)
        self.tree = archive_git.git(self.remote, 'mktree', input=b'').decode().strip()
        self.main = archive_git.git(self.remote, 'commit-tree', self.tree, input=b'Trusted main\n').decode().strip()
        archive_git.git(self.remote, 'update-ref', archive_git.MAIN_REF, self.main)
        self.latest = None
        self.verified = None
        self.counter = 100
        self.records = Mock()
        self.records.main_commit.return_value = self.main
        self.records.latest.side_effect = lambda: self.latest
        self.records.latest_verified.side_effect = lambda: self.verified
        self.records.is_verified.side_effect = lambda identifier: bool(self.verified and self.verified['id'] == identifier)
        self.records.archive_commit.side_effect = self.archive_head
        self.records.create.side_effect = self.create_record
        self.records.status.side_effect = self.status
        self.records.last_refresh.return_value = None
        self.records.client = SimpleNamespace(api_remaining=None, metrics={'rest_requests': 0, 'rest_bytes': 0},
                                              raw_file=self.read_raw_archive_file)
        self.api = API([issue()])
        self.api.client = SimpleNamespace(metrics={'rest_requests': 0, 'rest_bytes': 0})
        self.url = URL
        self.patch('publish.ROOT', self.fixture.root)
        self.patch('publish.STATE', self.fixture.root / '.scratch/build-state.json')
        self.patch('publish.revision', side_effect=lambda ref: self.main if ref == 'HEAD' else self.tree)
        self.patch('publish.utc_now', return_value='2026-10-14T12:00:00Z')
        self.patch('publish.worktree_clean', return_value=True)
        self.patch('release.time.sleep')
        self.patch('archive_git.init_remote', side_effect=self.init_remote)
        self.patch('archive_git.read_remote_snapshot', side_effect=lambda repository, commit, destination:
                   (archive_git.snapshot_from_git(self.remote, commit, destination), {'archive_transfer_bytes': 1}))
        self.patch('publish.prepare_archive', side_effect=lambda groups, previous, destination, **kwargs:
            archive_transaction.prepare_archive(groups, previous, destination, client_factory=self.fixture.api.client, **kwargs))
        patcher = patch.dict(os.environ, MAIN_COMMIT=self.main, GITHUB_RUN_ID='60', GITHUB_RUN_ATTEMPT='1',
                             GITHUB_REPOSITORY='course/site', GITHUB_TOKEN='fixture-token', PAGES_URL=self.url)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The workflow pins its producing attempt. Each fixture starts without
        # that inherited pin; rerun tests opt into their own producing attempt.
        os.environ.pop('EXPECTED_PRODUCER_ATTEMPT', None)
        os.environ.pop('DDD_PUBLICATION_PAUSED', None)
        self.serial = 0

    def patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, name):
        self.serial += 1
        return self.root / f'{name}-{self.serial}'

    def archive_head(self):
        try:
            return archive_git.git(self.remote, 'rev-parse', '--verify', archive_git.ARCHIVE_REF).decode().strip()
        except PublicationError:
            return None

    def archive_parents(self):
        return archive_git.git(self.remote, 'rev-list', '--parents', '-n', '1', self.archive_head()).decode().split()[1:]

    def init_remote(self, folder, repository):
        folder.mkdir()
        archive_git.git(folder, 'init', '--bare', '--quiet')
        archive_git.git(folder, 'remote', 'add', 'origin', str(self.remote))
        return folder

    def read_archive(self, destination):
        return archive_git.snapshot_from_git(self.remote, self.archive_head(), destination)

    def read_raw_archive_file(self, repository, commit, path, limit):
        self.assertEqual((repository, path), ('course/site', 'publication.json'))
        payload = archive_git.git(self.remote, 'show', commit + ':' + path)
        if len(payload) > limit:
            raise AssertionError('fixture archive manifest exceeds requested bound')
        return payload

    def create_record(self, release):
        self.counter += 1
        self.latest = {'id': self.counter, 'payload': copy.deepcopy(release)}
        return self.counter

    def status(self, identifier, state, run):
        if state == 'success':
            self.assertEqual(identifier, self.latest['id'])
            self.verified = copy.deepcopy(self.latest)

    def admission(self, event='schedule', issue_number=1):
        folder = self.folder('admission')
        digest = controller.admit(self.records, self.api, folder, event, issue_number=issue_number,
                                  requests_enabled=True)
        return folder, digest

    def preparation(self):
        admitted, checksum = self.admission()
        folder = self.folder('prepared')
        digest = controller.prepare(self.records, self.url, admitted, checksum, folder)
        return folder, digest

    def accepted(self, preparation, digest):
        folder = self.folder('accepted')
        checksum = controller.accept(self.records, preparation, digest, folder)
        return folder, checksum

    def publish(self):
        prepared, digest = self.preparation()
        metadata = controller.read_json(prepared / 'metadata.json')
        if 'candidate.tar' in metadata['files']:
            source, checksum = self.accepted(prepared, digest)
        else:
            source, checksum = prepared, digest
        controller.build(self.records, self.url, source, checksum)
        if controller.state()['status'] == 'prepared':
            controller.record(self.records)
            controller.authorize(self.records)
            self.fixture.deploy()
            controller.verify(self.records)
        result = self.folder('result')
        checksum = controller.finalize(source, checksum, result)
        return result, checksum

    def next_run(self, run_id):
        os.environ['GITHUB_RUN_ID'] = str(run_id)

    def test_first_run_with_empty_registry_creates_a_root_archive_and_verified_release(self):
        self.fixture.registry()
        self.assertIsNone(self.archive_head())
        self.publish()
        self.assertEqual(controller.state()['status'], 'verified')
        head = self.archive_head()
        self.assertEqual(self.archive_parents(), [])
        self.assertEqual(json.loads(archive_git.git(self.remote, 'show', head + ':publication.json')),
                         {'version': 2, 'groups': {}})
        self.assertEqual(self.latest['payload']['archive_commit'], head)
        self.assertEqual(self.verified, self.latest)
        self.assertEqual(entry_slugs(self.fixture.live), [])

    def test_first_run_publishes_a_live_group_and_answers_its_request(self):
        self.fixture.registry('alpha')
        result, checksum = self.publish()
        self.assertEqual(controller.state()['status'], 'verified')
        self.assertEqual(self.archive_parents(), [])
        self.assertEqual((self.fixture.live / 'alpha/text.md').read_bytes(), TEXT)
        self.assertEqual(entry_slugs(self.fixture.live), ['alpha'])
        self.assertEqual(self.api.issues[1]['state'], 'open')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')
        self.assertIn('is published', self.api.comments[1][0]['body'])

    def test_record_without_archive_branch_stops_before_import(self):
        self.latest = {'id': 7, 'payload': {
            'version': 2, 'run_id': '50', 'run_attempt': '1', 'main_commit': self.main, 'main_tree': self.tree,
            'archive_commit': 'c' * 40, 'archive_manifest_sha256': 'd' * 64, 'served_manifest_sha256': 'e' * 64,
            'site_sha256': 'f' * 64, 'probes': []}}
        self.verified = copy.deepcopy(self.latest)
        with patch.object(controller, 'prepare_archive', side_effect=AssertionError('must stop before import')), \
                self.assertRaisesRegex(PublicationError, 'student-work.*course-publication|course-publication.*student-work'):
            self.preparation()
        self.assertIsNone(self.archive_head())

    def test_archive_branch_without_record_publishes_the_accepted_archive(self):
        # A first run accepted its archive, then failed before recording a release.
        prepared, digest = self.preparation()
        self.accepted(prepared, digest)
        head = self.archive_head()
        self.assertIsNone(self.latest)
        self.next_run(61)
        self.publish()
        self.assertEqual(controller.state()['status'], 'verified')
        self.assertEqual(self.archive_head(), head)
        self.assertEqual(self.latest['payload']['archive_commit'], head)

    def test_concurrent_first_run_cannot_replace_an_archive_created_meanwhile(self):
        prepared, digest = self.preparation()
        self.next_run(61)
        other, other_digest = self.preparation()
        self.accepted(other, other_digest)
        head = self.archive_head()
        self.next_run(60)
        with self.assertRaisesRegex(PublicationError, 'archive head changed'):
            self.accepted(prepared, digest)
        self.assertEqual(self.archive_head(), head)

    def test_pause_blocks_archive_entry_and_issue_answer_before_remote_calls(self):
        self.records.reset_mock()
        with patch.dict(os.environ, DDD_PUBLICATION_PAUSED='true'):
            with self.assertRaisesRegex(PublicationError, 'paused'):
                controller.require_main(self.records)
            with self.assertRaisesRegex(PublicationError, 'paused'):
                controller.answer(self.api, self.folder('unused'), 'f' * 64, self.records)
        self.records.main_commit.assert_not_called()
        self.assertFalse(self.api.comments)

    def test_stage_metrics_survive_fresh_clients_checkpoints_and_nested_authorization(self):
        def counted_main():
            self.records.client.metrics['rest_requests'] += 1
            self.records.client.metrics['rest_bytes'] += 20
            return self.main

        def fresh_client():
            self.records.client = SimpleNamespace(api_remaining=None, metrics={'rest_requests': 0, 'rest_bytes': 0})

        self.records.main_commit.side_effect = counted_main
        prepared, digest = self.preparation()
        fresh_client()
        accepted, checksum = self.accepted(prepared, digest)
        fresh_client()
        controller.build(self.records, self.url, accepted, checksum)
        fresh_client()
        controller.record(self.records)
        fresh_client()
        controller.authorize(self.records)
        self.fixture.deploy()
        fresh_client()
        controller.verify(self.records)
        result = self.folder('result')
        checksum = controller.finalize(accepted, checksum, result)
        _, _, report = controller.load(result, checksum, {'result'})
        for stage, calls in {'prepare': 1, 'accept': 1, 'build': 1, 'record': 1,
                             'authorize': 1, 'verify': 2}.items():
            self.assertEqual(report['transport'][stage + '_api'],
                             {'rest_requests': calls, 'rest_bytes': calls * 20})

    def test_unaccepted_candidate_cannot_be_built(self):
        self.publish()
        self.fixture.update('alpha', b'Changed alpha')
        self.next_run(61)
        prepared, digest = self.preparation()
        head = self.archive_head()
        with self.assertRaisesRegex(PublicationError, 'unarchived'):
            controller.build(self.records, self.url, prepared, digest)
        self.assertEqual(self.archive_head(), head)

    def test_pending_attempt_reconstructs_after_artifacts_expire_before_another_import(self):
        prepared, digest = self.preparation()
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        expected = self.fixture.bytes_at(self.fixture.output)
        original_release = copy.deepcopy(self.latest['payload'])
        shutil.rmtree(self.fixture.output)
        shutil.rmtree(prepared)
        shutil.rmtree(accepted)
        self.next_run(62)
        with patch.object(controller, 'prepare_archive', side_effect=AssertionError('must retry before importing')):
            result, checksum = self.publish()
        self.assertEqual(self.fixture.bytes_at(self.fixture.output), expected)
        self.assertEqual(self.latest['payload'], original_release)
        self.assertEqual(controller.state()['status'], 'recovered')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')

    def test_deployment_verified_late_repairs_status_without_importing_or_redeploying(self):
        prepared, digest = self.preparation()
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        self.fixture.deploy()
        self.assertIsNone(self.verified)
        self.next_run(63)
        with patch.object(controller, 'prepare_archive', side_effect=AssertionError('verify pending first')):
            result, checksum = self.publish()
        self.assertEqual(controller.state()['status'], 'recovered')
        self.assertEqual(self.latest, self.verified)
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')

    def test_malformed_issue_never_starts_full_import_and_cooldown_is_reserved_before_import(self):
        self.api.issues[1]['body'] = '### Group\nunknown'
        admitted, checksum = self.admission(event='issues')
        data, batch, report = controller.load(admitted, checksum, {'admission'})
        self.assertEqual(data['data']['mode'], 'invalid')
        self.records.reserve.assert_not_called()
        controller.answer(self.api, admitted, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')
        self.api.issues[1] = issue()
        self.records.last_refresh.return_value = '2026-10-14T11:59:59Z'
        admitted, checksum = self.admission(event='issues')
        self.assertEqual(controller.load(admitted, checksum, {'admission'})[0]['data']['mode'], 'deferred')
        self.records.reserve.assert_not_called()
        self.records.last_refresh.return_value = None
        self.admission(event='issues')
        self.records.reserve.assert_called_once()

    def test_changed_artifact_run_identity_and_code_are_rejected(self):
        admitted, checksum = self.admission(event='workflow_dispatch')
        (admitted / 'batch.json').write_text('{}')
        with self.assertRaises(PublicationError): controller.load(admitted, checksum, {'admission'})
        fresh, checksum = self.admission(event='workflow_dispatch')
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        with self.assertRaises(PublicationError): controller.load(fresh, checksum, {'admission'})
        os.environ['GITHUB_RUN_ATTEMPT'] = '1'
        self.records.main_commit.return_value = 'f' * 40
        with self.assertRaisesRegex(PublicationError, 'superseded'):
            controller.prepare(self.records, self.url, fresh, checksum, self.folder('superseded'))

    def test_second_run_after_the_first_is_unchanged_and_answers_without_new_deployment(self):
        self.publish()
        previous = copy.deepcopy(self.latest)
        head = self.archive_head()
        self.next_run(64)
        result, checksum = self.publish()
        self.assertEqual(controller.state()['data']['mode'], 'unchanged')
        self.assertEqual(self.latest, previous)
        self.assertEqual(self.archive_head(), head)
        self.assertEqual(controller.state()['status'], 'verified')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')

    def test_release_probes_against_last_verified_archive_after_failed_new_code_attempt(self):
        self.publish()
        failed = copy.deepcopy(self.latest)
        failed['id'] += 1
        failed['payload']['main_commit'] = 'f' * 40
        self.latest = failed  # A newer attempted release must not replace the served baseline.
        self.counter = failed['id']
        self.fixture.update('beta', b'New beta text',
                            visual=SVG.replace(b'width="10"', b'width="11"'))
        self.next_run(61)
        self.publish()
        self.assertEqual(self.latest['payload']['probes'], ['beta/text.md', 'beta/visual.svg'])

    def test_idle_verified_pair_does_not_rebuild_site_on_a_clean_runner(self):
        self.publish()
        shutil.rmtree(self.fixture.output)
        self.next_run(67)
        with patch.object(archive_transaction, 'projected_site', side_effect=AssertionError('idle must not build')):
            result, checksum = self.publish()
        _, _, report = controller.load(result, checksum, {'result'})
        self.assertEqual(controller.state()['data']['mode'], 'unchanged')
        self.assertEqual(report['transport']['import']['archive_read_bytes'], 0)
        self.assertFalse(self.fixture.output.exists())

    def test_answer_rerun_names_producing_attempt_and_repairs_failed_close_without_duplicates(self):
        result, checksum = self.publish()
        self.api.fail_close = True
        with self.assertRaises(RuntimeError): controller.answer(self.api, result, checksum, self.records)
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        os.environ['EXPECTED_PRODUCER_ATTEMPT'] = '1'
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')
        self.assertEqual(len(self.api.comments[1]), 1)

    def test_rerun_build_repairs_its_attempted_pair_but_never_rolls_back_a_newer_release(self):
        prepared, digest = self.preparation()
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        release = copy.deepcopy(self.latest['payload'])
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        os.environ['EXPECTED_PRODUCER_ATTEMPT'] = '1'
        controller.build(self.records, self.url, accepted, checksum)
        self.assertEqual(controller.state()['release'], release)
        self.latest['payload']['archive_commit'] = 'f' * 40
        with self.assertRaisesRegex(PublicationError, 'superseded'):
            controller.build(self.records, self.url, accepted, checksum)

    def test_main_change_before_deploy_or_answer_leaves_requests_open(self):
        result, checksum = self.publish()
        self.records.main_commit.return_value = 'f' * 40
        with self.assertRaisesRegex(PublicationError, 'superseded'): controller.authorize(self.records)
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')
        self.assertFalse(self.api.comments)

    def test_requests_during_import_survive_burst_deferral_and_next_sweep(self):
        admitted, digest = self.admission()
        self.api.issues[2] = issue(2, created_at='2026-10-14T12:00:01Z', updated_at='2026-10-14T12:00:01Z')
        self.api.issues[3] = issue(3, body='### Group\n\nbeta', created_at='2026-10-14T12:00:02Z', updated_at='2026-10-14T12:00:02Z')
        prepared = self.folder('prepared')
        digest = controller.prepare(self.records, self.url, admitted, digest, prepared)
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        self.fixture.deploy()
        controller.verify(self.records)
        result = self.folder('result')
        checksum = controller.finalize(accepted, checksum, result)
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual([self.api.issues[n]['state'] for n in (1, 2, 3)], ['closed', 'open', 'open'])
        self.records.last_refresh.return_value = '2026-10-14T12:00:00Z'
        with patch.object(controller, 'utc_now', return_value='2026-10-14T12:00:03Z'):
            deferred, digest = self.admission(event='issues', issue_number=2)
            metadata, batch, _ = controller.load(deferred, digest, {'admission'})
            self.assertEqual(metadata['data']['mode'], 'deferred')
            self.assertEqual([request['number'] for request in batch['requests']], [2, 3])
        self.fixture.update('alpha', b'Late committed content')
        self.next_run(66)
        with patch.object(controller, 'utc_now', return_value='2026-10-14T12:10:00Z'):
            result, checksum = self.publish()
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual([self.api.issues[n]['state'] for n in (1, 2, 3)], ['closed', 'closed', 'closed'])
        self.assertEqual((self.fixture.live / 'alpha/text.md').read_bytes(), b'Late committed content')

    def test_removed_registry_row_leaves_the_archive_and_the_site(self):
        self.publish()
        self.fixture.registry('alpha')
        self.next_run(61)
        self.publish()
        archive = self.read_archive(self.folder('after-removal'))
        self.assertEqual(set(archive.groups), {'alpha'})
        self.assertFalse((self.fixture.live / 'beta').exists())
        self.assertEqual(entry_slugs(self.fixture.live), ['alpha'])


if __name__ == '__main__':
    unittest.main()
