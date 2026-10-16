import copy
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import archive_git
import archive_rebind
import archive_transaction
import publish_archive as controller
from github_content import ImportFailure
from publication_state import RecoveryError
from test_import_groups import SVG
from test_publication_requests import API, issue
import test_publish_site as fixtures


class SplitPublisherTests(unittest.TestCase):
    def setUp(self):
        self.publishing = fixtures.PublishingTests()
        self.publishing.setUp()
        self.addCleanup(self.publishing.doCleanups)
        self.publishing.bootstrap()
        self.fixture = self.publishing.fixture
        self.root = self.fixture.workspace
        self.remote = self.root / 'course.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(self.remote)], check=True)
        self.tree = archive_git.git(self.remote, 'mktree', input=b'').decode().strip()
        self.main = archive_git.git(self.remote, 'commit-tree', self.tree, input=b'Trusted main\n').decode().strip()
        archive_git.git(self.remote, 'update-ref', archive_git.MAIN_REF, self.main)
        self.latest = copy.deepcopy(self.publishing.records.latest.return_value)
        self.verified = copy.deepcopy(self.latest)
        self.counter = self.latest['id']
        self.records = Mock()
        self.records.main_commit.return_value = self.main
        self.records.latest.side_effect = lambda: self.latest
        self.records.latest_verified.side_effect = lambda: self.verified
        self.records.is_verified.side_effect = lambda identifier: bool(self.verified and self.verified['id'] == identifier)
        self.records.archive_commit.side_effect = self.archive_head
        self.records.archive_authority.side_effect = lambda commit: archive_git.accepted_authority(self.remote, commit)
        self.records.create.side_effect = self.create_record
        self.records.status.side_effect = self.status
        self.records.last_refresh.return_value = None
        self.records.client = SimpleNamespace(api_remaining=None, metrics={'rest_requests': 0, 'rest_bytes': 0},
                                              raw_file=self.read_raw_archive_file)
        self.api = API([issue()])
        self.api.client = SimpleNamespace(metrics={'rest_requests': 0, 'rest_bytes': 0})
        self.url = self.publishing.url
        self.patch('publish_archive.ROOT', self.fixture.root)
        self.patch('publish_archive.STATE', self.fixture.root / '.scratch/v2-build-state.json')
        self.patch('publish_archive.revision', side_effect=lambda ref: self.main if ref == 'HEAD' else self.tree)
        self.patch('publish_archive.utc_now', return_value='2026-10-14T12:00:00Z')
        self.patch('archive_git.init_remote', side_effect=self.init_remote)
        self.patch('publish_archive._read_archive', side_effect=self.read_archive)
        self.patch('publish_archive.prepare_archive', side_effect=lambda groups, previous, destination, **kwargs:
            archive_transaction.prepare_archive(groups, previous, destination, client_factory=self.fixture.api.client, **kwargs))
        patcher = patch.dict(os.environ, MAIN_COMMIT=self.main, GITHUB_RUN_ID='60', GITHUB_RUN_ATTEMPT='1',
                             GITHUB_REPOSITORY='course/site', GITHUB_TOKEN='fixture-token', PAGES_URL=self.url)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The workflow pins its producing attempt. Each fixture starts without
        # that inherited pin; rerun tests opt into their own producing attempt.
        os.environ.pop('EXPECTED_PRODUCER_ATTEMPT', None)
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
        except RecoveryError:
            raise ImportFailure('unavailable_repository', 'archive absent')

    def init_remote(self, folder, repository):
        folder.mkdir()
        archive_git.git(folder, 'init', '--bare', '--quiet')
        archive_git.git(folder, 'remote', 'add', 'origin', str(self.remote))
        return folder

    def read_archive(self, records, destination, commit=None):
        return archive_git.snapshot_from_git(self.remote, commit or self.archive_head(), destination), {'archive_transfer_bytes': 1}

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

    def admission(self, seed=False, event='schedule', issue_number=1, reconcile_seed=False, archive_enabled=False,
                  rebind=False):
        folder = self.folder('admission')
        digest = controller.admit(self.records, self.api, folder, event, issue_number=issue_number,
                                  requests_enabled=True, seed=seed, reconcile_seed=reconcile_seed,
                                  archive_enabled=archive_enabled, rebind=rebind)
        return folder, digest

    def preparation(self, seed=False):
        admitted, checksum = self.admission(seed, 'workflow_dispatch' if seed else 'schedule')
        folder = self.folder('prepared')
        digest = controller.prepare(self.records, self.url, admitted, checksum, folder)
        return folder, digest

    def accepted(self, preparation, digest, *, archive_enabled=False):
        folder = self.folder('accepted')
        checksum = controller.accept(self.records, self.url, preparation, digest, folder, writes_enabled=True,
                                     archive_enabled=archive_enabled)
        return folder, checksum

    def seed(self):
        prepared, digest = self.preparation(seed=True)
        return self.accepted(prepared, digest)

    def rebind_plan(self, repository='targetcourse/new-alpha'):
        groups_path = self.fixture.root / 'groups.json'
        groups = json.loads(groups_path.read_text())
        next(row for row in groups['groups'] if row['slug'] == 'alpha')['repository'] = repository
        groups_path.write_text(json.dumps(groups))
        previous, _ = self.read_archive(self.records, self.folder('rebind-parent'))
        derived, spec = archive_rebind.prepare_rebind(previous, controller.registry(), 'alpha',
            'course/alpha', 'main', self.folder('rebind-derived'))
        self.assertEqual(derived.groups[spec['retired_slug']]['snapshot'],
                         previous.groups['alpha']['snapshot'])
        github = self.fixture.root / '.github'
        github.mkdir(exist_ok=True)
        (github / 'archive-rebind.json').write_text(json.dumps(spec, indent=2) + '\n')
        return previous, spec

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

    def test_seed_refresh_accept_deploy_verify_and_answer_are_separate_gates(self):
        old_live = self.fixture.bytes_at(self.fixture.live)
        self.seed()
        self.assertEqual(self.fixture.bytes_at(self.fixture.live), old_live)
        self.assertEqual(self.latest['payload']['version'], 1)
        os.environ['GITHUB_RUN_ID'] = '61'
        result, checksum = self.publish()
        self.assertEqual(controller.state()['status'], 'verified')
        self.assertEqual(self.latest['payload']['version'], 2)
        self.assertEqual(self.latest['payload']['archive_commit'], self.archive_head())
        self.assertEqual(self.api.issues[1]['state'], 'open')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')
        self.assertIn('is published', self.api.comments[1][0]['body'])

    def test_pause_blocks_archive_entry_and_issue_answer_before_remote_calls(self):
        self.records.reset_mock()
        with patch.dict(os.environ, DDD_PUBLICATION_PAUSED='true'):
            with self.assertRaisesRegex(RecoveryError, 'paused'):
                controller.require_main(self.records)
            with self.assertRaisesRegex(RecoveryError, 'paused'):
                controller.answer(self.api, self.folder('unused'), 'f' * 64, self.records)
        self.records.main_commit.assert_not_called()
        self.assertFalse(self.api.comments)

    def test_seed_downloads_each_distinct_blob_once_and_reports_all_pages_transport(self):
        self.fixture.server.requests.clear()
        prepared, checksum = self.preparation(seed=True)
        _, _, report = controller.load(prepared, checksum, {'prepared'})
        paths = [urlsplit(request.full_url).path.removeprefix('/course/')
                 for request in self.fixture.server.requests]
        manifest = json.loads((self.fixture.live / 'publication.json').read_bytes())
        unique_blobs = {record['sha256'] for entry in manifest['groups'].values()
                        for record in entry['files'].values()}
        content_paths = [path for path in paths if path not in {'publication.json', 'release.json'}]
        self.assertEqual(len(content_paths), len(unique_blobs))
        self.assertEqual(report['transport']['seed_pages'], {
            'pages_requests': len(paths),
            'pages_bytes': sum((self.fixture.live / path).stat().st_size for path in paths)})
        self.assertEqual(set(path.name for path in prepared.iterdir()),
                         {'metadata.json', 'batch.json', 'report.json', 'candidate.tar', 'seed-manifest.json'})

    def test_seed_cache_is_private_and_corruption_is_refetched_before_materialization(self):
        original_seed = controller.seed_v1
        destination = self.folder('seed')
        caches = []

        def corrupt_after_validation(payload, groups, read):
            result = original_seed(payload, groups, read)
            caches.extend(destination.parent.glob('ddd-v1-seed-cache-*'))
            self.assertEqual(len(caches), 1)
            for path in caches[0].iterdir():
                path.write_bytes(b'x' * path.stat().st_size)
            return result

        with patch.object(controller, 'seed_v1', side_effect=corrupt_after_validation):
            snapshot, _, _ = controller._seed(self.records, self.url, destination)
        self.assertFalse(caches[0].exists())
        self.assertEqual(set(path.name for path in snapshot.folder.iterdir()),
                         {'alpha', 'beta', 'publication.json'})
        for slug in ('alpha', 'beta'):
            self.assertEqual(self.fixture.bytes_at(snapshot.folder / slug),
                             self.fixture.bytes_at(self.fixture.live / slug))

    def test_seed_cache_is_removed_when_validation_fails(self):
        destination = self.folder('seed')
        with patch.object(controller, 'seed_v1', side_effect=RecoveryError('invalid captured bytes')):
            with self.assertRaisesRegex(RecoveryError, 'invalid captured bytes'):
                controller._seed(self.records, self.url, destination)
        self.assertFalse(list(destination.parent.glob('ddd-v1-seed-cache-*')))

    def test_stage_metrics_survive_fresh_clients_checkpoints_and_nested_authorization(self):
        def counted_main():
            self.records.client.metrics['rest_requests'] += 1
            self.records.client.metrics['rest_bytes'] += 20
            return self.main

        def fresh_client():
            self.records.client = SimpleNamespace(api_remaining=None, metrics={'rest_requests': 0, 'rest_bytes': 0})

        self.records.main_commit.side_effect = counted_main
        prepared, digest = self.preparation(seed=True)
        _, _, report = controller.load(prepared, digest, {'prepared'})
        self.assertEqual(report['transport']['prepare_api']['rest_requests'], 1)
        fresh_client()
        self.accepted(prepared, digest)
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

    def test_failed_archive_write_cannot_build_unaccepted_content(self):
        self.seed()
        prepared, digest = self.preparation()
        head = self.archive_head()
        with self.assertRaisesRegex(RecoveryError, 'disabled'):
            controller.accept(self.records, self.url, prepared, digest, self.folder('denied'), writes_enabled=False)
        with self.assertRaisesRegex(RecoveryError, 'unarchived'):
            controller.build(self.records, self.url, prepared, digest)
        self.assertEqual(self.archive_head(), head)
        self.assertEqual(self.latest['payload']['version'], 1)

    def test_pending_attempt_reconstructs_after_artifacts_expire_before_another_import(self):
        self.seed()
        prepared, digest = self.preparation()
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        expected = self.fixture.bytes_at(self.fixture.output)
        original_release = copy.deepcopy(self.latest['payload'])
        shutil.rmtree(self.fixture.output)
        shutil.rmtree(prepared)
        shutil.rmtree(accepted)
        os.environ['GITHUB_RUN_ID'] = '62'
        with patch.object(controller, 'prepare_archive', side_effect=AssertionError('must retry before importing')):
            result, checksum = self.publish()
        self.assertEqual(self.fixture.bytes_at(self.fixture.output), expected)
        self.assertEqual(self.latest['payload'], original_release)
        self.assertEqual(controller.state()['status'], 'recovered')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')

    def test_deployment_verified_late_repairs_status_without_importing_or_redeploying(self):
        self.seed()
        prepared, digest = self.preparation()
        accepted, checksum = self.accepted(prepared, digest)
        controller.build(self.records, self.url, accepted, checksum)
        controller.record(self.records)
        self.fixture.deploy()
        self.assertNotEqual(self.latest['id'], self.verified['id'])
        os.environ['GITHUB_RUN_ID'] = '63'
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

    def test_changed_code_run_identity_artifact_and_seed_are_rejected(self):
        admitted, checksum = self.admission(seed=True, event='workflow_dispatch')
        (admitted / 'batch.json').write_text('{}')
        with self.assertRaises(RecoveryError): controller.load(admitted, checksum, {'admission'})
        fresh, checksum = self.admission(seed=True, event='workflow_dispatch')
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        with self.assertRaises(RecoveryError): controller.load(fresh, checksum, {'admission'})
        os.environ['GITHUB_RUN_ATTEMPT'] = '1'
        self.records.main_commit.return_value = 'f' * 40
        with self.assertRaisesRegex(RecoveryError, 'superseded'):
            controller.prepare(self.records, self.url, fresh, checksum, self.folder('superseded'))

    def test_unchanged_full_refresh_verifies_and_answers_without_new_deployment(self):
        self.seed()
        self.publish()
        previous = copy.deepcopy(self.latest)
        self.api.issues[1] = issue()
        os.environ['GITHUB_RUN_ID'] = '64'
        result, checksum = self.publish()
        self.assertEqual(controller.state()['data']['mode'], 'unchanged')
        self.assertEqual(self.latest, previous)
        self.assertEqual(controller.state()['status'], 'verified')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')

    def test_release_probes_against_last_verified_archive_after_failed_new_code_attempt(self):
        self.seed()
        self.publish()  # Establish the first verified V2 archive and served baseline.
        failed = copy.deepcopy(self.latest)
        failed['id'] += 1
        failed['payload']['main_commit'] = 'f' * 40
        self.latest = failed  # A newer attempted release must not replace the served baseline.
        self.counter = failed['id']
        self.fixture.update('beta', b'New beta text',
                            visual=SVG.replace(b'width="10"', b'width="11"'))
        self.publish()
        self.assertEqual(self.latest['payload']['probes'], ['beta/text.md', 'beta/visual.svg'])

    def test_idle_verified_pair_does_not_rebuild_site_on_a_clean_runner(self):
        self.seed()
        self.publish()
        shutil.rmtree(self.fixture.output)
        os.environ['GITHUB_RUN_ID'] = '67'
        with patch.object(archive_transaction, 'projected_site', side_effect=AssertionError('idle must not build')):
            result, checksum = self.publish()
        _, _, report = controller.load(result, checksum, {'result'})
        self.assertEqual(controller.state()['data']['mode'], 'unchanged')
        self.assertEqual(report['transport']['import']['archive_read_bytes'], 0)
        self.assertFalse(self.fixture.output.exists())

    def test_acceptance_survives_missing_build_record_before_first_cutover(self):
        self.seed()
        prepared, digest = self.preparation()
        self.accepted(prepared, digest)
        self.assertEqual(self.latest['payload']['version'], 1)
        self.records.archive_main.return_value = self.main
        os.environ['GITHUB_RUN_ID'] = '65'
        with patch.object(controller, 'prepare_archive', side_effect=AssertionError('publish pending acceptance first')):
            result, checksum = self.publish()
        self.assertEqual(controller.state()['data']['mode'], 'pending')
        self.assertEqual(controller.state()['status'], 'recovered')
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')

    def test_answer_rerun_names_producing_attempt_and_repairs_failed_close_without_duplicates(self):
        self.seed()
        result, checksum = self.publish()
        self.api.fail_close = True
        with self.assertRaises(RuntimeError): controller.answer(self.api, result, checksum, self.records)
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        os.environ['EXPECTED_PRODUCER_ATTEMPT'] = '1'
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'closed')
        self.assertEqual(len(self.api.comments[1]), 1)

    def test_rerun_build_repairs_its_attempted_pair_but_never_rolls_back_a_newer_release(self):
        self.seed()
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
        with self.assertRaisesRegex(RecoveryError, 'superseded'):
            controller.build(self.records, self.url, accepted, checksum)

    def test_main_change_before_deploy_or_answer_leaves_requests_open(self):
        self.seed()
        result, checksum = self.publish()
        self.records.main_commit.return_value = 'f' * 40
        with self.assertRaisesRegex(RecoveryError, 'superseded'): controller.authorize(self.records)
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual(self.api.issues[1]['state'], 'open')
        self.assertFalse(self.api.comments)

    def test_requests_during_import_survive_burst_deferral_and_next_sweep(self):
        self.seed()
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
        os.environ['GITHUB_RUN_ID'] = '66'
        with patch.object(controller, 'utc_now', return_value='2026-10-14T12:10:00Z'):
            result, checksum = self.publish()
        controller.answer(self.api, result, checksum, self.records)
        self.assertEqual([self.api.issues[n]['state'] for n in (1, 2, 3)], ['closed', 'closed', 'closed'])
        self.assertEqual((self.fixture.live / 'alpha/text.md').read_bytes(), b'Late committed content')

    def test_cutover_rejects_a_seed_that_v1_publication_has_outgrown(self):
        self.seed()
        self.fixture.update('alpha', b'New verified V1 content')
        self.publishing.prepare()
        self.publishing.record()
        self.fixture.deploy()
        import publish_site
        publish_site.verify(self.publishing.records)
        self.latest = copy.deepcopy(self.publishing.records.latest.return_value)
        self.verified = copy.deepcopy(self.latest)
        with self.assertRaisesRegex(RecoveryError, 'seed is stale'):
            self.preparation()

    def advance_v1(self, *, remove_beta=False, content=b'New verified V1 content'):
        self.fixture.update('alpha', content)
        if remove_beta:
            self.fixture.registry('alpha')
        self.publishing.prepare()
        self.publishing.record()
        self.fixture.deploy()
        import publish_site
        publish_site.verify(self.publishing.records)
        self.latest = copy.deepcopy(self.publishing.records.latest.return_value)
        self.verified = copy.deepcopy(self.latest)

    def reconciled_seed(self):
        admitted, checksum = self.admission(event='workflow_dispatch', reconcile_seed=True)
        prepared = self.folder('reconciled')
        checksum = controller.prepare(self.records, self.url, admitted, checksum, prepared)
        return prepared, checksum

    def test_stale_seed_reconciliation_preserves_parent_and_hidden_removed_bytes_without_deploying(self):
        self.seed()
        parent = self.archive_head()
        old_beta = self.fixture.bytes_at(self.fixture.live / 'beta')
        self.advance_v1(remove_beta=True)
        live = self.fixture.bytes_at(self.fixture.live)
        self.fixture.api.routes.clear()  # served V1 and archive are sufficient
        prepared, checksum = self.reconciled_seed()
        accepted, checksum = self.accepted(prepared, checksum)
        head = self.archive_head()
        self.assertEqual(archive_git.git(self.remote, 'rev-parse', head + '^').decode().strip(), parent)
        folder = self.folder('read-reconciled')
        snapshot, _ = self.read_archive(self.records, folder)
        self.assertEqual(snapshot.read_file('alpha', 'text.md'), b'New verified V1 content')
        self.assertEqual(self.fixture.bytes_at(folder / 'beta'), old_beta)
        self.assertEqual(snapshot.groups['beta']['state'], 'hidden')
        self.assertNotIn('beta', snapshot.served())
        controller.build(self.records, self.url, accepted, checksum)
        self.assertEqual(controller.state()['status'], 'reconcile_seed')
        self.assertEqual(self.fixture.bytes_at(self.fixture.live), live)
        self.assertEqual(self.latest['payload']['version'], 1)

    def test_reconciliation_rechecks_latest_v1_before_writer_and_rejects_v2_parent(self):
        self.seed()
        self.advance_v1(content=b'Newer verified V1 content')
        prepared, checksum = self.reconciled_seed()
        parent = self.archive_head()
        self.advance_v1()
        with self.assertRaisesRegex(RecoveryError, 'latest verified V1'):
            self.accepted(prepared, checksum)
        self.assertEqual(self.archive_head(), parent)
        # Reconcile and cut over, then neither explicit seed mode may overwrite V2.
        prepared, checksum = self.reconciled_seed()
        self.accepted(prepared, checksum)
        self.publish()
        with self.assertRaisesRegex(RecoveryError, 'V2 archive authority'):
            self.reconciled_seed()

    def test_seed_reconciliation_requires_explicit_nonconflicting_dispatch(self):
        with self.assertRaisesRegex(RecoveryError, 'event'):
            self.admission(reconcile_seed=True)
        with self.assertRaisesRegex(RecoveryError, 'event'):
            self.admission(seed=True, reconcile_seed=True, event='workflow_dispatch')

    def test_seed_modes_are_disabled_after_v2_cutover_at_admission_and_writer(self):
        with self.assertRaisesRegex(RecoveryError, 'after archive cutover'):
            self.admission(seed=True, event='workflow_dispatch', archive_enabled=True)
        with self.assertRaisesRegex(RecoveryError, 'after archive cutover'):
            self.admission(reconcile_seed=True, event='workflow_dispatch', archive_enabled=True)
        prepared, checksum = self.preparation(seed=True)
        with self.assertRaisesRegex(RecoveryError, 'after archive cutover'):
            self.accepted(prepared, checksum, archive_enabled=True)

    def test_rebind_accepts_reviewed_migration_and_preserves_retired_snapshot_without_deploying(self):
        self.seed()
        self.publish()
        live = self.fixture.bytes_at(self.fixture.live)
        release_id = self.latest['id']
        previous, spec = self.rebind_plan()
        admitted, digest = self.admission(event='workflow_dispatch', rebind=True, archive_enabled=True)
        prepared = self.folder('rebind-prepared')
        digest = controller.prepare(self.records, self.url, admitted, digest, prepared)
        accepted, checksum = self.accepted(prepared, digest, archive_enabled=True)

        metadata, _, _ = controller.load(accepted, checksum, {'accepted'})
        commit = metadata['data']['archive_commit']
        self.assertEqual(self.archive_head(), commit)
        self.assertEqual(archive_git.accepted_authority(self.remote, commit), 'v2')
        migrated, _ = self.read_archive(self.records, self.folder('rebound-archive'))
        retired = migrated.groups[spec['retired_slug']]
        self.assertEqual(retired['state'], 'hidden')
        self.assertEqual(retired['snapshot'], previous.groups['alpha']['snapshot'])
        for name in previous.groups['alpha']['snapshot']['files']:
            self.assertEqual(migrated.read_file(spec['retired_slug'], name), previous.read_file('alpha', name))
        self.assertEqual(migrated.groups['alpha']['repository'], 'targetcourse/new-alpha')
        self.assertEqual(migrated.groups['alpha']['state'], 'live')
        self.assertIsNone(migrated.groups['alpha']['snapshot'])
        self.assertEqual(migrated.groups['beta'], previous.groups['beta'])
        self.assertEqual(self.fixture.bytes_at(self.fixture.live), live)
        self.assertEqual(self.latest['id'], release_id)
        self.assertEqual(self.latest['payload']['version'], 2)

        self.fixture.api.add('targetcourse/new-alpha', {'text.md': b'new repository content', 'visual.svg': SVG})
        # First publish the accepted rebind snapshot, then a regular refresh can
        # import the new identity because this fixture now supplies its source.
        self.publish()
        self.assertEqual(self.latest['payload']['archive_commit'], commit)
        self.publish()
        refreshed, _ = self.read_archive(self.records, self.folder('rebound-refresh'))
        self.assertIsNotNone(refreshed.groups['alpha']['snapshot'])
        self.assertEqual(refreshed.groups['alpha']['repository'], 'targetcourse/new-alpha')
        self.assertEqual(refreshed.read_file('alpha', 'text.md'), b'new repository content')

    def test_rebind_rejects_wrong_parent_at_prepare_and_altered_plan_at_writer(self):
        self.seed()
        self.publish()
        previous, spec = self.rebind_plan()
        spec['expected_parent'] = 'f' * 40
        (self.fixture.root / '.github/archive-rebind.json').write_text(json.dumps(spec))
        admitted, digest = self.admission(event='workflow_dispatch', rebind=True, archive_enabled=True)
        with self.assertRaisesRegex(RecoveryError, 'reviewed rebind plan differs'):
            controller.prepare(self.records, self.url, admitted, digest, self.folder('wrong-parent'))
        self.assertEqual(self.archive_head(), previous.commit)

        _, spec = self.rebind_plan()
        admitted, digest = self.admission(event='workflow_dispatch', rebind=True, archive_enabled=True)
        prepared = self.folder('valid-rebind')
        digest = controller.prepare(self.records, self.url, admitted, digest, prepared)
        spec['new_repository'] = 'attacker/repo'
        (self.fixture.root / '.github/archive-rebind.json').write_text(json.dumps(spec))
        with self.assertRaisesRegex(RecoveryError, 'rebind identity differs'):
            self.accepted(prepared, digest, archive_enabled=True)
        self.assertEqual(self.archive_head(), previous.commit)

    def test_rebind_requires_archive_authority_and_nonconflicting_dispatch(self):
        with self.assertRaisesRegex(RecoveryError, 'requires archive authority'):
            self.admission(event='workflow_dispatch', rebind=True)
        with self.assertRaisesRegex(RecoveryError, 'event'):
            self.admission(rebind=True)
        with self.assertRaisesRegex(RecoveryError, 'event'):
            self.admission(seed=True, rebind=True, event='workflow_dispatch', archive_enabled=True)

    def test_rebind_rejects_archive_ahead_of_publication_and_unverified_latest(self):
        self.seed()
        self.publish()
        self.rebind_plan()
        actual_parent = self.archive_head()
        self.latest['payload']['archive_commit'] = 'f' * 40
        admitted, digest = self.admission(event='workflow_dispatch', rebind=True, archive_enabled=True)
        with self.assertRaisesRegex(RecoveryError, 'archive is ahead of verified publication'):
            controller.prepare(self.records, self.url, admitted, digest, self.folder('archive-ahead'))
        self.assertEqual(self.archive_head(), actual_parent)

        self.latest['payload']['archive_commit'] = actual_parent
        self.verified = None
        admitted, digest = self.admission(event='workflow_dispatch', rebind=True, archive_enabled=True)
        with self.assertRaisesRegex(RecoveryError, 'verified V2 release'):
            controller.prepare(self.records, self.url, admitted, digest, self.folder('unverified-latest'))


if __name__ == '__main__':
    unittest.main()
