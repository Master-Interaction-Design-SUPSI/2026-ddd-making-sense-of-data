import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import publication_rollback as rollback
import publication_recovery
import test_publish_site as publish_fixtures
from publication_recovery import capture_v1
from publication_state import RecoveryError
from publish_site import encode
import archive_git
from archive_release import make_release
from archive_state import ArchiveSnapshot, encode_archive, seed_v1
from group_registry import load_registry


class FakeRecords:
    def __init__(self, v2_release, archive='c' * 40, main='a' * 40):
        self.v2_release = v2_release
        self.archive = archive
        self.main = main
        self.v2_id = 77
        self.rollback_id = 0
        self.rollback_payload = None
        self.statuses = []
        self.create_calls = []

    def main_commit(self):
        return self.main

    def archive_commit(self):
        return self.archive

    def latest(self):
        return {'id': self.v2_id, 'payload': self.v2_release}

    def is_verified(self, identifier):
        return identifier == self.v2_id

    def create(self, payload):
        self.create_calls.append(copy.deepcopy(payload))
        self.rollback_id += 1
        self.rollback_payload = copy.deepcopy(payload)
        return self.rollback_id

    def status(self, identifier, status, run_url):
        self.statuses.append((identifier, status, run_url))

    def rollback_latest(self):
        if not self.rollback_id:
            return None
        return {'id': self.rollback_id, 'payload': self.rollback_payload}


class FakeRegularRecords:
    def __init__(self, release, archive, main, tree):
        self.main, self.archive, self.tree = main, archive, tree
        self.old = {'id': 77, 'payload': release}
        self.current = self.old.copy()
        self.verified = self.old
        self.statuses = []
        self.created = []

    def main_commit(self):
        return self.main

    def archive_commit(self):
        return self.archive

    def latest(self):
        return self.current

    def latest_verified(self):
        return self.verified

    def is_verified(self, identifier):
        return bool(self.verified and self.verified['id'] == identifier)

    def create(self, release):
        identifier = 80 + len(self.created)
        row = {'id': identifier, 'payload': copy.deepcopy(release)}
        self.created.append(row)
        self.current = row
        return identifier

    def status(self, identifier, status, run_url):
        self.statuses.append((identifier, status, run_url))
        if status == 'success' and self.current and self.current['id'] == identifier:
            self.verified = self.current


class FakeRollbackHistory:
    def __init__(self, identifier, payload):
        self.row = {'id': identifier, 'payload': copy.deepcopy(payload)}

    def rollback_latest(self):
        return self.row

    def is_verified(self, identifier):
        return identifier == self.row['id']


class PublicationRollbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publish_fixtures.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bootstrap()
        self.workspace = self.fixture.fixture.workspace
        self.site = self.fixture.fixture.output
        self.release = json.loads((self.site / 'release.json').read_bytes())
        self.capture = self.workspace / 'original-v1-capture'
        capture_v1(self.site, self.release, self.capture)
        self.archive = 'c' * 40
        self.v2 = {
            'version': 2, 'run_id': '80', 'run_attempt': '1',
            'main_commit': 'a' * 40, 'main_tree': 'b' * 40,
            'archive_commit': self.archive, 'archive_manifest_sha256': 'd' * 64,
            'served_manifest_sha256': 'e' * 64, 'site_sha256': 'f' * 64,
            'probes': [],
        }
        self.records = FakeRecords(self.v2, self.archive)
        self.plan = {
            'expected_archive_commit': self.archive,
            'expected_v2_deployment_id': self.records.v2_id,
            'expected_v2_release': self.v2,
            'expected_v1_release': self.release,
            'capture_sha256': hashlib.sha256((self.capture / 'capture.json').read_bytes()).hexdigest(),
        }
        self.stage = self.workspace / 'rollback-stage'

    def prepare(self, **kwargs):
        return rollback.prepare(self.records, self.fixture.url, self.capture, self.plan, self.stage,
                                paused=True, main_commit='a' * 40, run_id='90', run_attempt='2', **kwargs)

    def test_stages_capture_records_separately_and_verifies_all_served_bytes(self):
        # A disposable archive ref is the V2 head named by the plan.
        repository = self.workspace / 'v2-archive.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(repository)], check=True)
        work = self.workspace / 'v2-archive-work'
        work.mkdir()
        subprocess.run(['git', '-C', str(work), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(work), 'config', 'user.name', 'Rollback test'], check=True)
        subprocess.run(['git', '-C', str(work), 'config', 'user.email', 'rollback@example.test'], check=True)
        (work / 'publication.json').write_text('preserved V2 archive')
        subprocess.run(['git', '-C', str(work), 'add', 'publication.json'], check=True)
        subprocess.run(['git', '-C', str(work), 'commit', '-q', '-m', 'V2 accepted'], check=True)
        subprocess.run(['git', '-C', str(work), 'branch', '-M', 'student-work'], check=True)
        actual = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
        self.records.archive = actual
        self.plan['expected_archive_commit'] = actual
        self.v2['archive_commit'] = actual
        self.plan['expected_v2_release'] = self.v2
        before = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'refs/heads/student-work'], text=True).strip()

        state = self.prepare()
        self.assertEqual(state['status'], 'prepared')
        self.assertEqual(self.records.create_calls, [])
        self.assertEqual(self.records.statuses, [])
        staged = {path.relative_to(self.stage / 'site').as_posix(): path.read_bytes()
                  for path in (self.stage / 'site').rglob('*') if path.is_file()}
        captured = {path.relative_to(self.capture / 'site').as_posix(): path.read_bytes()
                    for path in (self.capture / 'site').rglob('*') if path.is_file()}
        self.assertEqual(staged, captured)

        state = rollback.record(self.records, state, paused=True, run_url='local-run')
        self.assertEqual(state['status'], 'recorded')
        payload = self.records.create_calls[0]
        self.assertEqual(payload['release'], self.release)
        self.assertEqual(payload['archive_head_to_preserve'], actual)
        self.assertEqual(payload['v2_deployment_id'], self.records.v2_id)
        self.assertEqual(self.records.statuses, [(1, 'in_progress', 'local-run')])
        self.assertEqual(self.records.latest()['payload'], self.v2)

        state = rollback.authorize(self.records, state, paused=True)
        self.assertEqual(state['status'], 'authorized')
        # Fixture Pages serves the original V1 bytes. Full inventory verification is real.
        result = rollback.verify_served(self.fixture.url, self.capture, self.release)
        self.assertEqual(result['site_files_verified'], len(
            [name for name in publication_recovery.verify_capture(self.capture)['inventory']
             if name not in {'release.json', 'publication.json'}]))
        state = rollback.verify(self.records, state, paused=True, run_url='verified-run',
                                verifier=lambda *_args: result)
        self.assertEqual(state['status'], 'verified')
        self.assertEqual(self.records.statuses[-1], (1, 'success', 'verified-run'))
        self.assertEqual(self.records.latest()['payload'], self.v2)
        after = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'refs/heads/student-work'], text=True).strip()
        self.assertEqual(after, before)

    def test_pause_and_v1_v2_schema_checks_fail_before_any_write(self):
        with self.assertRaisesRegex(RecoveryError, 'pause'):
            rollback.prepare(self.records, self.fixture.url, self.capture, self.plan, self.stage,
                             paused=False, main_commit='a' * 40)
        v2_as_v1 = self.plan | {'expected_v1_release': self.v2}
        with self.assertRaisesRegex(RecoveryError, 'expected a V1'):
            rollback.prepare(self.records, self.fixture.url, self.capture, v2_as_v1, self.stage,
                             paused=True, main_commit='a' * 40)
        self.assertEqual(self.records.create_calls, [])

    def test_tampered_capture_and_stale_predeploy_authorities_are_rejected(self):
        (self.capture / 'site' / 'alpha' / 'text.md').write_bytes(b'tampered')
        with self.assertRaises(RecoveryError):
            self.prepare()
        shutil.rmtree(self.capture)
        capture_v1(self.site, self.release, self.capture)
        self.plan['capture_sha256'] = hashlib.sha256((self.capture / 'capture.json').read_bytes()).hexdigest()

        self.records.archive = '9' * 40
        with self.assertRaisesRegex(RecoveryError, 'archive head'):
            self.prepare()
        self.records.archive = self.archive
        self.records.v2_id += 1
        with self.assertRaisesRegex(RecoveryError, 'deployment'):
            self.prepare()
        self.assertEqual(self.records.create_calls, [])

    def test_pause_and_authority_drift_after_staging_blocks_authorization(self):
        state = self.prepare()
        with self.assertRaisesRegex(RecoveryError, 'pause'):
            rollback.record(self.records, state, paused=False)
        state = rollback.record(self.records, state, paused=True)
        self.records.archive = '9' * 40
        with self.assertRaisesRegex(RecoveryError, 'archive head'):
            rollback.authorize(self.records, state, paused=True)
        self.assertFalse(any(status == 'success' for _identifier, status, _url in self.records.statuses))

    def test_record_retry_adopts_only_an_exact_existing_intent_and_missing_record_blocks(self):
        state = self.prepare()
        expected_payload = rollback._rollback_payload(state)
        self.records.rollback_id = 1
        self.records.rollback_payload = copy.deepcopy(expected_payload)
        state = rollback.record(self.records, state, paused=True)
        self.assertEqual(state['deployment_id'], 1)
        self.assertEqual(self.records.create_calls, [])
        self.assertEqual(self.records.statuses, [(1, 'in_progress', '')])

        state = rollback.authorize(self.records, state, paused=True)
        self.records.rollback_id = 0
        self.records.rollback_payload = None
        state['status'] = 'recorded'
        with self.assertRaisesRegex(RecoveryError, 'rollback-point record changed'):
            rollback.authorize(self.records, state, paused=True)

    def test_record_rejects_a_conflicting_intent_from_the_same_run_attempt(self):
        state = self.prepare()
        conflicting = rollback._rollback_payload(state) | {'capture_sha256': '1' * 64}
        self.records.rollback_id = 5
        self.records.rollback_payload = conflicting
        with self.assertRaisesRegex(RecoveryError, 'different rollback intent'):
            rollback.record(self.records, state, paused=True)
        self.assertEqual(self.records.create_calls, [])

    def test_postdeploy_archive_drift_never_marks_rollback_success(self):
        state = self.prepare()
        state = rollback.record(self.records, state, paused=True)
        state = rollback.authorize(self.records, state, paused=True)

        def deploy_then_advance(*_args):
            self.records.archive = '9' * 40
            return {'published_files_verified': 3}

        with self.assertRaisesRegex(RecoveryError, 'archive head'):
            rollback.verify(self.records, state, paused=True, verifier=deploy_then_advance)
        self.assertFalse(any(status == 'success' for _identifier, status, _url in self.records.statuses))

    def test_postdeploy_main_or_v2_payload_drift_never_marks_success(self):
        state = self.prepare()
        state = rollback.record(self.records, state, paused=True)
        state = rollback.authorize(self.records, state, paused=True)

        def deploy_then_advance(*_args):
            self.records.v2_release = self.v2 | {'site_sha256': '1' * 64}
            return {'published_files_verified': 3}

        with self.assertRaisesRegex(RecoveryError, 'deployment'):
            rollback.verify(self.records, state, paused=True, verifier=deploy_then_advance)
        self.assertFalse(any(status == 'success' for _identifier, status, _url in self.records.statuses))

    def test_full_served_verification_detects_one_changed_student_file(self):
        self.fixture.fixture.deploy()
        path = self.fixture.fixture.live / 'alpha/text.md'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaisesRegex(RecoveryError, 'content differs'):
            rollback.verify_served(self.fixture.url, self.capture, self.release, deadline_seconds=5)

    def test_rollback_records_use_separate_task_and_reject_v2_payload_as_rollback(self):
        record_client = rollback.RollbackRecords.__new__(rollback.RollbackRecords)
        record_client.base = '/repos/course/site'
        record_client.client = Mock()
        record_client.client.get.return_value = json.dumps([{
            'id': 123, 'task': rollback.ROLLBACK_TASK,
            'environment': rollback.ROLLBACK_ENVIRONMENT,
            'payload': {
                'version': 1, 'operation': 'verified-v1-visible-rollback-point',
                'release': self.release,
                'capture_sha256': self.plan['capture_sha256'],
                'archive_head_to_preserve': self.archive,
                'v2_deployment_id': self.records.v2_id,
                'v2_release_sha256': hashlib.sha256(encode(self.v2)).hexdigest(),
                'v2_release': self.v2, 'main_commit': 'a' * 40,
                'run_id': '90', 'run_attempt': '2',
            },
        }]).encode()
        self.assertEqual(record_client.rollback_latest()['task'], rollback.ROLLBACK_TASK)
        query = record_client.client.get.call_args.args[0]
        self.assertIn('task=course-publication-rollback', query)
        bad = record_client.client.get.return_value
        row = json.loads(bad)
        row[0]['payload']['release'] = self.v2
        record_client.client.get.return_value = json.dumps(row).encode()
        with self.assertRaisesRegex(RecoveryError, 'V1'):
            record_client.rollback_latest()

    def test_cli_cannot_override_a_missing_publication_pause(self):
        environment = dict(os.environ)
        environment.pop('DDD_PUBLICATION_PAUSED', None)
        script = Path(rollback.__file__)
        result = subprocess.run(
            [sys.executable, str(script), 'prepare', '--state', str(self.workspace / 'state.json'),
             '--paused', 'true'],
            capture_output=True, text=True, env=environment, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('must be true', result.stderr)

    def test_resume_cli_prepare_binds_plan_to_exact_head_tree_and_run_context(self):
        commit, tree = 'a' * 40, 'b' * 40
        plan = {
            'version': 1, 'operation': 'resume-v2-after-v1-rollback',
            'rollback_deployment_id': 5, 'rollback_payload_sha256': '1' * 64,
            'expected_archive_commit': 'c' * 40, 'expected_v2_deployment_id': 77,
            'expected_v2_release_sha256': '2' * 64,
        }
        plan_path = self.workspace / 'archive-resume.json'
        plan_path.write_bytes(encode(plan))
        state_path = self.workspace / 'resume-state.json'
        output_path = self.workspace / 'github-output.txt'
        stage = self.workspace / 'cli-stage'
        output = __import__('io').StringIO()
        with unittest.mock.patch.dict(os.environ, {
                'DDD_PUBLICATION_PAUSED': 'true', 'MAIN_COMMIT': commit,
                'GITHUB_REPOSITORY': 'course/site', 'GITHUB_RUN_ID': '90',
                'GITHUB_RUN_ATTEMPT': '2', 'PAGES_URL': self.fixture.url,
                'GITHUB_OUTPUT': str(output_path)}), \
             unittest.mock.patch('publication_rollback.git_revision', side_effect=[commit, tree]), \
             unittest.mock.patch('publication_rollback.worktree_clean', return_value=True), \
             unittest.mock.patch('publication_rollback.RollbackRecords'), \
             unittest.mock.patch('publication_rollback.DeploymentRecords'), \
             unittest.mock.patch('publication_rollback.prepare_resume',
                                 return_value={'cli': 'ok', 'status': 'prepared'}) as prepare, \
             unittest.mock.patch('sys.stdout', output):
            rollback.main(['resume-prepare', '--state', str(state_path), '--reviewed-plan', str(plan_path),
                           '--stage', str(stage), '--site-url', self.fixture.url])
        self.assertEqual(json.loads(output.getvalue()), {'cli': 'ok', 'status': 'prepared'})
        self.assertEqual(json.loads(state_path.read_bytes()), {'cli': 'ok', 'status': 'prepared'})
        self.assertEqual(output_path.read_text(), 'resume_complete=false\n')
        self.assertEqual(prepare.call_args.kwargs['main_commit'], commit)
        self.assertEqual(prepare.call_args.kwargs['main_tree'], tree)
        self.assertEqual(prepare.call_args.kwargs['run_id'], '90')
        self.assertEqual(prepare.call_args.kwargs['run_attempt'], '2')

    def _resume_fixture(self, main='a' * 40, tree='b' * 40):
        groups = seed_v1((self.fixture.fixture.live / 'publication.json').read_bytes(),
            load_registry(self.fixture.fixture.root / 'groups.json'),
            lambda slug, name: (self.fixture.fixture.live / slug / name).read_bytes())
        archive_folder = self.workspace / 'resume-archive'
        archive_folder.mkdir()
        for slug in groups:
            shutil.copytree(self.fixture.fixture.live / slug, archive_folder / slug)
        (archive_folder / 'publication.json').write_bytes(encode_archive(groups))
        archive = ArchiveSnapshot(archive_folder, self.archive)
        old_v2 = make_release('a' * 40, 'b' * 40, archive, '80', '1')
        regular = FakeRegularRecords(old_v2, self.archive, main, tree)
        rollback_payload = {
            'version': 1, 'operation': 'verified-v1-visible-rollback-point',
            'release': self.release, 'capture_sha256': self.plan['capture_sha256'],
            'archive_head_to_preserve': self.archive, 'v2_deployment_id': 77,
            'v2_release_sha256': hashlib.sha256(encode(old_v2)).hexdigest(),
            'v2_release': old_v2, 'main_commit': main, 'run_id': '90', 'run_attempt': '2',
        }
        rollback_history = FakeRollbackHistory(5, rollback_payload)
        plan = {
            'version': 1, 'operation': 'resume-v2-after-v1-rollback',
            'rollback_deployment_id': 5,
            'rollback_payload_sha256': hashlib.sha256(encode(rollback_payload)).hexdigest(),
            'expected_archive_commit': self.archive, 'expected_v2_deployment_id': 77,
            'expected_v2_release_sha256': hashlib.sha256(encode(old_v2)).hexdigest(),
        }
        return archive, old_v2, regular, rollback_history, plan

    def _prepare_resume(self, *, main='a' * 40, tree='b' * 40):
        archive, old_v2, regular, history, plan = self._resume_fixture(main, tree)
        destination = self.workspace / 'resume-stage'
        state = rollback.prepare_resume(
            regular, history, lambda _commit, _destination: archive, plan, destination,
            paused=True, main_commit=main, main_tree=tree, run_id='90', run_attempt='2',
            site_url=self.fixture.url, site_code_root=self.fixture.fixture.root)
        return state, archive, old_v2, regular, history, plan

    def test_resume_rebuilds_from_pinned_archive_and_records_regular_v2_separately(self):
        state, archive, old_v2, regular, history, _plan = self._prepare_resume()
        self.assertEqual(state['pair_mode'], 'exact-recorded-pair')
        self.assertEqual(state['release'], old_v2)
        self.assertEqual(regular.archive_commit(), archive.commit)
        self.assertEqual(regular.current['payload'], old_v2)
        state = rollback.record_resume(regular, history, state, paused=True, run_url='resume')
        self.assertEqual(state['status'], 'recorded')
        self.assertEqual(regular.created, [{'id': 80, 'payload': old_v2}])
        self.assertEqual(regular.current['payload'], old_v2)
        state = rollback.authorize_resume(regular, history, state, paused=True)
        state = rollback.verify_resume(regular, history, state, paused=True, run_url='verified',
                                       verifier=lambda _url, release: {'probes_verified': len(release['probes'])})
        self.assertEqual(state['status'], 'verified')
        self.assertEqual(regular.statuses[-1], (80, 'success', 'verified'))
        self.assertEqual(regular.archive_commit(), self.archive)
        self.assertEqual(history.rollback_latest()['payload']['operation'], 'verified-v1-visible-rollback-point')

    def test_resume_loader_reads_real_local_bare_archive_without_precreating_destination(self):
        _source_archive, _old, regular, history, plan = self._resume_fixture()
        bare = self.workspace / 'archive.git'
        work = self.workspace / 'archive-work'
        subprocess.run(['git', 'init', '--bare', '-q', str(bare)], check=True)
        work.mkdir()
        subprocess.run(['git', '-C', str(work), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(work), 'config', 'user.name', 'Resume test'], check=True)
        subprocess.run(['git', '-C', str(work), 'config', 'user.email', 'resume@example.test'], check=True)
        # Build the exact durable archive snapshot from accepted V1 files.
        groups = seed_v1((self.fixture.fixture.live / 'publication.json').read_bytes(),
            load_registry(self.fixture.fixture.root / 'groups.json'),
            lambda slug, name: (self.fixture.fixture.live / slug / name).read_bytes())
        for slug in groups:
            shutil.copytree(self.fixture.fixture.live / slug, work / slug)
        (work / 'publication.json').write_bytes(encode_archive(groups))
        subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(work), 'commit', '-q', '-m', 'Pinned archive'], check=True)
        commit = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
        subprocess.run(['git', '-C', str(work), 'remote', 'add', 'origin', str(bare)], check=True)
        subprocess.run(['git', '-C', str(work), 'push', '-q', 'origin', 'HEAD:refs/heads/student-work'], check=True)

        snapshot_destination = self.workspace / 'bare-snapshot'
        self.assertFalse(snapshot_destination.exists())
        archive = archive_git.snapshot_from_git(bare, commit, snapshot_destination)
        self.assertTrue(snapshot_destination.is_dir())
        old_v2 = make_release('a' * 40, 'b' * 40, archive, '80', '1')
        regular.archive = commit
        regular.old = {'id': 77, 'payload': old_v2}
        regular.current = regular.old
        regular.verified = regular.old
        rollback_payload = history.row['payload'] | {
            'archive_head_to_preserve': commit,
            'v2_release_sha256': hashlib.sha256(encode(old_v2)).hexdigest(),
            'v2_release': old_v2,
        }
        history.row['payload'] = rollback_payload
        plan = plan | {
            'expected_archive_commit': commit,
            'expected_v2_release_sha256': hashlib.sha256(encode(old_v2)).hexdigest(),
            'rollback_payload_sha256': hashlib.sha256(encode(rollback_payload)).hexdigest(),
        }
        before = subprocess.check_output(['git', '--git-dir', str(bare), 'rev-parse',
                                          'refs/heads/student-work'], text=True).strip()
        stage = self.workspace / 'bare-resume-stage'
        def loader(pin, destination):
            self.assertFalse(Path(destination).exists())
            return archive_git.snapshot_from_git(bare, pin, destination)
        state = rollback.prepare_resume(
            regular, history, loader, plan, stage, paused=True,
            main_commit='a' * 40, main_tree='b' * 40, run_id='90', run_attempt='2',
            site_url=self.fixture.url, site_code_root=self.fixture.fixture.root)
        after = subprocess.check_output(['git', '--git-dir', str(bare), 'rev-parse',
                                         'refs/heads/student-work'], text=True).strip()
        self.assertEqual(state['release'], old_v2)
        self.assertEqual(after, before)
        self.assertEqual(list(self.workspace.glob('.v2-resume-*')), [])

    def test_resume_cleans_temporary_material_when_pinned_archive_load_fails(self):
        _archive, _old_v2, regular, history, plan = self._resume_fixture()
        destination = self.workspace / 'archive-failure-stage'
        def fail_loader(_commit, _destination):
            raise RecoveryError('test loader failure')
        with self.assertRaisesRegex(RecoveryError, 'test loader failure'):
            rollback.prepare_resume(
                regular, history, fail_loader, plan, destination, paused=True,
                main_commit='a' * 40, main_tree='b' * 40, run_id='90', run_attempt='2',
                site_url=self.fixture.url, site_code_root=self.fixture.fixture.root)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.workspace.glob('.v2-resume-*')), [])

    def test_resume_supersedes_durable_pair_when_code_pin_changes_without_source_import(self):
        main, tree = 'd' * 40, 'e' * 40
        archive, old_v2, regular, history, plan = self._resume_fixture(main, tree)
        # The rollback point remains tied to its old V2 pair; only current trusted code advances.
        history.row['payload']['main_commit'] = 'a' * 40
        plan['rollback_payload_sha256'] = hashlib.sha256(encode(history.row['payload'])).hexdigest()
        destination = self.workspace / 'changed-code-resume-stage'
        original_archive = regular.archive
        with unittest.mock.patch('build_site.import_groups', side_effect=AssertionError('source import forbidden')):
            state = rollback.prepare_resume(
                regular, history, lambda _commit, _destination: archive, plan, destination,
                paused=True, main_commit=main, main_tree=tree, run_id='91', run_attempt='1',
                site_url=self.fixture.url, site_code_root=self.fixture.fixture.root)
        self.assertEqual(state['pair_mode'], 'superseding-current-code')
        self.assertNotEqual(state['release']['main_commit'], old_v2['main_commit'])
        self.assertEqual(regular.current['payload'], old_v2)
        self.assertEqual(regular.archive, original_archive)
        state = rollback.record_resume(regular, history, state, paused=True)
        self.assertEqual(regular.created[0]['payload'], state['release'])
        self.assertEqual(regular.latest_verified()['payload'], old_v2)
        rollback.authorize_resume(regular, history, state, paused=True)

    def test_resume_supersedes_an_older_unverified_attempt_with_changed_current_code(self):
        main, tree = 'd' * 40, 'e' * 40
        archive, old_v2, regular, history, plan = self._resume_fixture(main, tree)
        prior_pending = make_release('f' * 40, '1' * 40, archive, '81', '1')
        regular.current = {'id': 79, 'payload': prior_pending}
        destination = self.workspace / 'superseding-resume-stage'
        state = rollback.prepare_resume(
            regular, history, lambda _commit, _destination: archive, plan, destination,
            paused=True, main_commit=main, main_tree=tree, run_id='91', run_attempt='1',
            site_url=self.fixture.url, site_code_root=self.fixture.fixture.root)
        self.assertEqual(state['pair_mode'], 'superseding-current-code')
        self.assertEqual(state['predecessor_v2_deployment_id'], 79)
        self.assertEqual(state['release']['main_commit'], main)
        state = rollback.record_resume(regular, history, state, paused=True)
        self.assertEqual(regular.created[-1]['payload'], state['release'])
        self.assertNotEqual(regular.latest()['payload'], prior_pending)
        self.assertEqual(regular.latest_verified()['payload'], old_v2)

    def test_resume_prepare_recovers_success_status_after_local_state_loss(self):
        state, archive, _old_v2, regular, history, plan = self._prepare_resume()
        state = rollback.record_resume(regular, history, state, paused=True)
        state = rollback.authorize_resume(regular, history, state, paused=True)
        rollback.verify_resume(regular, history, state, paused=True,
                               verifier=lambda _url, _release: {'probes_verified': 1})
        self.assertEqual(regular.latest_verified()['id'], state['resume_deployment_id'])
        prior_statuses = list(regular.statuses)

        destination = self.workspace / 'resume-after-state-loss'
        reverified = rollback.prepare_resume(
            regular, history, lambda _commit, _destination: archive, plan, destination,
            paused=True, main_commit=state['main_commit'], main_tree=state['main_tree'],
            run_id='91', run_attempt='1', site_url=self.fixture.url,
            site_code_root=self.fixture.fixture.root,
            served_verifier=lambda _url, release: {'probes_verified': len(release['probes'])})
        self.assertEqual(reverified['status'], 'verified')
        self.assertEqual(reverified['resume_deployment_id'], state['resume_deployment_id'])
        self.assertEqual(regular.statuses, prior_statuses)
        self.assertEqual(regular.latest_verified()['payload'], reverified['release'])

    def test_resume_adopts_exact_pending_pair_after_retry_and_rejects_drift_or_unpaused(self):
        state, archive, _old_v2, regular, history, _plan = self._prepare_resume()
        pending = {'id': 93, 'payload': copy.deepcopy(state['release'])}
        regular.current = pending
        destination = self.workspace / 'resume-retry-stage'
        retried = rollback.prepare_resume(
            regular, history, lambda _commit, _destination: archive,
            _plan, destination, paused=True,
            main_commit=state['main_commit'], main_tree=state['main_tree'],
            run_id='92', run_attempt='1', site_url=self.fixture.url,
            site_code_root=self.fixture.fixture.root)
        retried = rollback.record_resume(regular, history, retried, paused=True)
        self.assertEqual(retried['resume_deployment_id'], 93)
        self.assertEqual(regular.created, [])

        with self.assertRaisesRegex(RecoveryError, 'pause'):
            rollback.authorize_resume(regular, history, retried, paused=False)
        regular.archive = '9' * 40
        with self.assertRaisesRegex(RecoveryError, 'archive head'):
            rollback.authorize_resume(regular, history, retried, paused=True)
        self.assertFalse(any(status == 'success' for _identifier, status, _url in regular.statuses))

    def test_resume_rejects_tampered_rollback_record_and_postdeploy_authority_drift(self):
        state, _archive, _old_v2, regular, history, _plan = self._prepare_resume()
        state = rollback.record_resume(regular, history, state, paused=True)
        state = rollback.authorize_resume(regular, history, state, paused=True)

        def deploy_then_tamper(_url, _release):
            history.row['payload']['capture_sha256'] = '1' * 64
            return {'probes_verified': 1}

        with self.assertRaisesRegex(RecoveryError, 'rollback point changed'):
            rollback.verify_resume(regular, history, state, paused=True, verifier=deploy_then_tamper)
        self.assertFalse(any(status == 'success' for _identifier, status, _url in regular.statuses))

        with self.assertRaises(RecoveryError):
            rollback.prepare_resume(regular, history, lambda *_: None, _plan,
                                    self.workspace / 'bad-stage', paused=False,
                                    main_commit=state['main_commit'], main_tree=state['main_tree'],
                                    site_url=self.fixture.url)


if __name__ == '__main__':
    unittest.main()
