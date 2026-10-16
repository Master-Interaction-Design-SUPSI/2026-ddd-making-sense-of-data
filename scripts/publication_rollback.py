"""Stage and verify a V1 visible rollback without changing V2 archive authority.

Rollback records use their own GitHub deployment task/environment. A successful
record means that captured V1 content was served byte-for-byte and the reviewed
V2 deployment, main pin, and archive head still matched afterward.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import archive_git
import archive_release
import build_site
from deployment_records import DeploymentRecords
from publication_recovery import CAPTURE, stage_rollback, verify_capture
from content_contract import MAX_SITE_BYTES
from publication_state import MANIFEST, MAX_MANIFEST_BYTES, RecoveryError, SiteReader, decode_manifest, unique_keys
from publish_site import RELEASE, encode, git_revision, site_digest, validate_release, worktree_clean


ROLLBACK_TASK = 'course-publication-rollback'
ROLLBACK_ENVIRONMENT = 'course-publication-rollback-point'
STATE_VERSION = 1
MAX_STATE_BYTES = 12 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent
SHA40 = re.compile(r'[0-9a-f]{40}')
SHA64 = re.compile(r'[0-9a-f]{64}')


def _sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def _require_paused(paused):
    if paused is not True:
        raise RecoveryError('V1 rollback requires the publication pause to be explicitly enabled')


def _capture_digest(capture):
    path = Path(capture)
    metadata = path / CAPTURE
    if path.is_symlink() or not path.is_dir() or metadata.is_symlink() or not metadata.is_file():
        raise RecoveryError('rollback capture is missing or unsafe')
    if metadata.stat().st_size > 8 * 1024 * 1024:
        raise RecoveryError('rollback capture metadata exceeds its limit')
    return _sha256(metadata.read_bytes())


def _release(value, version):
    try:
        result = validate_release(value)
    except (RecoveryError, TypeError, ValueError) as error:
        raise RecoveryError(f'invalid expected V{version} release') from error
    if result['version'] != version:
        raise RecoveryError(f'expected a V{version} release')
    return result


def validate_reviewed_plan(plan):
    required = {'expected_archive_commit', 'expected_v2_deployment_id', 'expected_v2_release',
                'expected_v1_release', 'capture_sha256'}
    if not isinstance(plan, dict) or set(plan) != required:
        raise RecoveryError('reviewed rollback plan has an unexpected schema')
    if not isinstance(plan['expected_archive_commit'], str) or not SHA40.fullmatch(plan['expected_archive_commit']):
        raise RecoveryError('reviewed plan needs the exact V2 archive commit')
    if type(plan['expected_v2_deployment_id']) is not int or plan['expected_v2_deployment_id'] <= 0:
        raise RecoveryError('reviewed plan needs an exact V2 deployment id')
    if not isinstance(plan['capture_sha256'], str) or not SHA64.fullmatch(plan['capture_sha256']):
        raise RecoveryError('reviewed plan needs the capture metadata digest')
    v2 = _release(plan['expected_v2_release'], 2)
    v1 = _release(plan['expected_v1_release'], 1)
    if v2['archive_commit'] != plan['expected_archive_commit']:
        raise RecoveryError('reviewed V2 release does not name the expected archive head')
    return plan | {'expected_v2_release': v2, 'expected_v1_release': v1}


def _assert_current_v2(records, state):
    if records.main_commit() != state['main_commit']:
        raise RecoveryError('main changed during rollback; restart from the reviewed code pin')
    latest = records.latest()
    if (not latest or latest.get('id') != state['expected_v2_deployment_id']
            or latest.get('payload') != state['expected_v2_release']):
        raise RecoveryError('latest V2 deployment changed during rollback')
    if records.archive_commit() != state['archive_head_to_preserve']:
        raise RecoveryError('V2 archive head changed during rollback')
    if not records.is_verified(state['expected_v2_deployment_id']):
        raise RecoveryError('reviewed V2 deployment is no longer verified')


def _rollback_payload(state):
    return {
        'version': 1,
        'operation': 'verified-v1-visible-rollback-point',
        'release': state['expected_v1_release'],
        'capture_sha256': state['capture_sha256'],
        'archive_head_to_preserve': state['archive_head_to_preserve'],
        'v2_deployment_id': state['expected_v2_deployment_id'],
        'v2_release_sha256': _sha256(encode(state['expected_v2_release'])),
        'v2_release': state['expected_v2_release'],
        'main_commit': state['main_commit'],
        'run_id': state['run_id'],
        'run_attempt': state['run_attempt'],
    }


def _validate_rollback_payload(payload):
    keys = {'version', 'operation', 'release', 'capture_sha256', 'archive_head_to_preserve',
            'v2_deployment_id', 'v2_release_sha256', 'v2_release', 'main_commit', 'run_id', 'run_attempt'}
    if (not isinstance(payload, dict) or set(payload) != keys or type(payload['version']) is not int
            or payload['version'] != 1 or payload['operation'] != 'verified-v1-visible-rollback-point'):
        raise RecoveryError('invalid rollback-point deployment payload')
    _release(payload['release'], 1)
    v2 = _release(payload['v2_release'], 2)
    if (not isinstance(payload['capture_sha256'], str) or not SHA64.fullmatch(payload['capture_sha256'])
            or not isinstance(payload['archive_head_to_preserve'], str)
            or not SHA40.fullmatch(payload['archive_head_to_preserve'])
            or type(payload['v2_deployment_id']) is not int or payload['v2_deployment_id'] <= 0
            or payload['v2_release_sha256'] != _sha256(encode(v2))
            or payload['archive_head_to_preserve'] != v2['archive_commit']
            or not isinstance(payload['main_commit'], str) or not SHA40.fullmatch(payload['main_commit'])
            or not isinstance(payload['run_id'], str) or not re.fullmatch(r'[1-9][0-9]*', payload['run_id'])
            or not isinstance(payload['run_attempt'], str) or not re.fullmatch(r'[1-9][0-9]*', payload['run_attempt'])):
        raise RecoveryError('invalid rollback-point deployment identity')
    return payload


class RollbackRecords(DeploymentRecords):
    """Independent durable rollback-point records, separate from V2 authority."""

    def rollback_latest(self):
        rows = json.loads(self.client.get(
            self.base + f'/deployments?task={ROLLBACK_TASK}&environment={ROLLBACK_ENVIRONMENT}&per_page=1',
            2 * 1024 * 1024))
        if not isinstance(rows, list) or len(rows) > 1:
            raise RecoveryError('GitHub returned invalid rollback-point history')
        if not rows:
            return None
        row = rows[0]
        if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                or row.get('task') != ROLLBACK_TASK or row.get('environment') != ROLLBACK_ENVIRONMENT):
            raise RecoveryError('GitHub returned an unexpected rollback-point record')
        _validate_rollback_payload(row.get('payload'))
        return row

    def create(self, payload):
        payload = _validate_rollback_payload(payload)
        row = self.post('/deployments', {
            'ref': payload['release']['source_commit'], 'task': ROLLBACK_TASK,
            'environment': ROLLBACK_ENVIRONMENT, 'auto_merge': False, 'required_contexts': [],
            'payload': payload, 'description': 'V1 rollback point; V2 archive authority preserved',
            'production_environment': False,
        })
        if (not isinstance(row, dict) or type(row.get('id')) is not int
                or row.get('task') != ROLLBACK_TASK or row.get('environment') != ROLLBACK_ENVIRONMENT
                or row.get('payload') != payload):
            raise RecoveryError('GitHub did not create the exact rollback-point record')
        return row['id']

    def status(self, identifier, state, run_url):
        if type(identifier) is not int or identifier <= 0 or state not in {'in_progress', 'success'}:
            raise RecoveryError('invalid rollback-point status')
        self.post(f'/deployments/{identifier}/statuses', {
            'state': state, 'log_url': run_url, 'auto_inactive': False,
            'description': ('V1 bytes verified; reviewed V2 archive head preserved'
                           if state == 'success' else 'V1 rollback deployment awaiting verification'),
        })


def prepare(records, url, capture, reviewed_plan, destination, *, paused=False,
            main_commit=None, run_id='1', run_attempt='1'):
    """Validate authorities and stage captured V1 bytes; perform no remote writes."""
    _require_paused(paused)
    plan = validate_reviewed_plan(reviewed_plan)
    if main_commit is None or not isinstance(main_commit, str) or not SHA40.fullmatch(main_commit):
        raise RecoveryError('rollback needs the exact trusted main commit')
    if records.main_commit() != main_commit:
        raise RecoveryError('trusted main commit changed or is superseded')
    if not isinstance(run_id, str) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise RecoveryError('invalid workflow run id')
    if not isinstance(run_attempt, str) or not re.fullmatch(r'[1-9][0-9]*', run_attempt):
        raise RecoveryError('invalid workflow run attempt')
    capture_path = Path(capture)
    metadata = verify_capture(capture_path, expected_release=plan['expected_v1_release'])
    capture_hash = _capture_digest(capture_path)
    if capture_hash != plan['capture_sha256']:
        raise RecoveryError('capture metadata digest differs from reviewed rollback plan')
    latest = records.latest()
    if (not latest or latest.get('id') != plan['expected_v2_deployment_id']
            or latest.get('payload') != plan['expected_v2_release']):
        raise RecoveryError('latest V2 deployment differs from reviewed rollback plan')
    if not records.is_verified(plan['expected_v2_deployment_id']):
        raise RecoveryError('reviewed V2 deployment is not verified')
    archive_head = records.archive_commit()
    if archive_head != plan['expected_archive_commit']:
        raise RecoveryError('current V2 archive head differs from reviewed rollback plan')
    destination = Path(destination)
    stage_rollback(capture_path, destination, archive_head, expected_release=plan['expected_v1_release'])
    if _capture_digest(capture_path) != capture_hash:
        raise RecoveryError('capture changed during rollback staging')
    state = {
        'version': STATE_VERSION, 'status': 'prepared', 'site_url': url,
        'capture': str(capture_path.resolve()), 'stage': str(destination.resolve()),
        'capture_sha256': capture_hash, 'expected_v1_release': plan['expected_v1_release'],
        'expected_v2_release': plan['expected_v2_release'],
        'expected_v2_deployment_id': plan['expected_v2_deployment_id'],
        'archive_head_to_preserve': archive_head, 'main_commit': main_commit,
        'run_id': run_id, 'run_attempt': run_attempt, 'deployment_id': None,
    }
    _assert_current_v2(records, state)
    return state


def _validate_state(state):
    required = {'version', 'status', 'site_url', 'capture', 'stage', 'capture_sha256',
                'expected_v1_release', 'expected_v2_release', 'expected_v2_deployment_id',
                'archive_head_to_preserve', 'main_commit', 'run_id', 'run_attempt', 'deployment_id'}
    if (not isinstance(state, dict) or not required <= set(state)
            or set(state) - required - {'verification'} or type(state['version']) is not int
            or state['version'] != STATE_VERSION
            or state['status'] not in {'prepared', 'recorded', 'authorized', 'verified'}):
        raise RecoveryError('invalid rollback controller state')
    if ((state['status'] == 'verified') != ('verification' in state)
            or (state['status'] == 'prepared') != (state['deployment_id'] is None)):
        raise RecoveryError('rollback state status differs from its deployment identity')
    if state['status'] in {'recorded', 'authorized', 'verified'} and state['deployment_id'] is None:
        raise RecoveryError('rollback state is missing its durable deployment identity')
    _release(state['expected_v1_release'], 1)
    v2 = _release(state['expected_v2_release'], 2)
    if (type(state['expected_v2_deployment_id']) is not int or state['expected_v2_deployment_id'] <= 0
            or state['archive_head_to_preserve'] != v2['archive_commit']
            or not isinstance(state['capture_sha256'], str) or not SHA64.fullmatch(state['capture_sha256'])
            or not isinstance(state['main_commit'], str) or not SHA40.fullmatch(state['main_commit'])
            or not isinstance(state['run_id'], str) or not re.fullmatch(r'[1-9][0-9]*', state['run_id'])
            or not isinstance(state['run_attempt'], str) or not re.fullmatch(r'[1-9][0-9]*', state['run_attempt'])
            or (state['deployment_id'] is not None and
                (type(state['deployment_id']) is not int or state['deployment_id'] <= 0))):
        raise RecoveryError('invalid rollback controller authority pins')
    return state


def _verify_stage(site, metadata):
    site = Path(site)
    if site.is_symlink() or not site.is_dir():
        raise RecoveryError('staged V1 site is missing or unsafe')
    expected = metadata['inventory']
    actual = {}
    entries = list(site.rglob('*'))
    if len(entries) > 10000:
        raise RecoveryError('staged V1 site exceeds its inventory limit')
    for path in entries:
        rel = path.relative_to(site).as_posix()
        if path.is_symlink() or not path.is_file() and not path.is_dir():
            raise RecoveryError('staged V1 site contains unsafe entries')
        if path.is_file():
            digest = hashlib.sha256()
            size = 0
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > expected.get(rel, {}).get('size', -1):
                        raise RecoveryError('staged V1 site differs from captured bytes')
            actual[rel] = {'size': size, 'sha256': digest.hexdigest()}
    if actual != expected:
        raise RecoveryError('staged V1 site differs from captured bytes')
    actual_directories = sorted(path.relative_to(site).as_posix() for path in entries if path.is_dir())
    if actual_directories != metadata['directories']:
        raise RecoveryError('staged V1 directory inventory differs from capture')


def _verify_local_material(state):
    state = _validate_state(state)
    capture = Path(state['capture'])
    if _capture_digest(capture) != state['capture_sha256']:
        raise RecoveryError('rollback capture changed after preparation')
    metadata = verify_capture(capture, expected_release=state['expected_v1_release'])
    _verify_stage(Path(state['stage']) / 'site', metadata)
    return metadata


def _expected_rollback_payload(state):
    return _rollback_payload(state)


def record(records, state, *, paused=False, run_url=''):
    _require_paused(paused)
    state = _validate_state(state)
    if state['status'] != 'prepared' or state['deployment_id'] is not None:
        raise RecoveryError('only an unrecorded prepared rollback may be recorded')
    _verify_local_material(state)
    _assert_current_v2(records, state)
    payload = _rollback_payload(state)
    existing = records.rollback_latest() if hasattr(records, 'rollback_latest') else None
    if existing and existing.get('payload') == payload:
        # A retry of this exact workflow attempt resolves an ambiguous create
        # response by adopting the record already durably present.
        identifier = existing['id']
    else:
        if (existing and isinstance(existing.get('payload'), dict)
                and existing['payload'].get('run_id') == state['run_id']
                and existing['payload'].get('run_attempt') == state['run_attempt']):
            raise RecoveryError('this workflow attempt already has a different rollback intent')
        identifier = records.create(payload)
    state['deployment_id'] = identifier
    state['status'] = 'recorded'
    records.status(identifier, 'in_progress', run_url)
    return state


def _assert_rollback_record(records, state):
    latest = records.rollback_latest() if hasattr(records, 'rollback_latest') else records.latest()
    if (not latest or latest.get('id') != state['deployment_id']
            or latest.get('payload') != _expected_rollback_payload(state)):
        raise RecoveryError('rollback-point record changed')


def authorize(records, state, *, paused=False):
    _require_paused(paused)
    state = _validate_state(state)
    if state['status'] != 'recorded' or state['deployment_id'] is None:
        raise RecoveryError('rollback must have a durable intent record before deployment')
    _verify_local_material(state)
    _assert_current_v2(records, state)
    _assert_rollback_record(records, state)
    state['status'] = 'authorized'
    return state


def verify_served(url, capture, expected_release, *, deadline_seconds=1200, reader_factory=SiteReader):
    """Check release, manifest, and every captured published file within 20 minutes."""
    release = _release(expected_release, 1)
    if type(deadline_seconds) not in {int, float} or deadline_seconds <= 0:
        raise RecoveryError('rollback verification deadline must be positive')
    deadline_seconds = min(deadline_seconds, 1200)
    metadata = verify_capture(capture, expected_release=release)
    manifest = (Path(capture) / 'site' / MANIFEST).read_bytes()
    decode_manifest(manifest)
    deadline = time.monotonic() + deadline_seconds
    reader = reader_factory(url)
    reader.overall_deadline = deadline
    served_release = json.loads(reader.read(RELEASE, 4096), object_pairs_hook=unique_keys)
    served_manifest = reader.read(MANIFEST, MAX_MANIFEST_BYTES)
    if served_release != release or _sha256(served_manifest) != release['manifest_sha256']:
        raise RecoveryError('served V1 release or manifest differs from the verified capture')
    expected_files = [(name, record) for name, record in metadata['inventory'].items()
                      if name not in {RELEASE, MANIFEST}]
    if sum(record['size'] for _name, record in expected_files) > MAX_SITE_BYTES:
        raise RecoveryError('captured served files exceed rollback verification byte bound')
    for name, record in sorted(expected_files):
        if time.monotonic() >= deadline:
            raise RecoveryError('full V1 rollback verification exceeded its 20-minute deadline')
        reader.deadline = min(deadline, time.monotonic() + 180)
        payload = reader.read(name, record['size'])
        if len(payload) != record['size'] or _sha256(payload) != record['sha256']:
            raise RecoveryError(f'served V1 content differs from capture: {name}')
    reader.deadline = min(deadline, time.monotonic() + 180)
    if json.loads(reader.read(RELEASE, 4096), object_pairs_hook=unique_keys) != release:
        raise RecoveryError('served V1 release changed during full-content verification')
    reader.deadline = min(deadline, time.monotonic() + 180)
    if _sha256(reader.read(MANIFEST, MAX_MANIFEST_BYTES)) != release['manifest_sha256']:
        raise RecoveryError('served V1 manifest changed during full-content verification')
    return {'release': release, 'manifest_sha256': _sha256(served_manifest),
            'site_files_verified': len(expected_files),
            'site_bytes_verified': sum(record['size'] for _name, record in expected_files)}


def verify(records, state, *, paused=False, run_url='', verifier=verify_served):
    _require_paused(paused)
    state = _validate_state(state)
    if state['status'] != 'authorized' or state['deployment_id'] is None:
        raise RecoveryError('rollback deployment was not authorized')
    _verify_local_material(state)
    _assert_current_v2(records, state)
    _assert_rollback_record(records, state)
    result = verifier(state['site_url'], state['capture'], state['expected_v1_release'])
    _verify_local_material(state)
    _assert_current_v2(records, state)
    _assert_rollback_record(records, state)
    records.status(state['deployment_id'], 'success', run_url)
    state['status'] = 'verified'
    state['verification'] = result
    return state


def validate_resume_plan(plan):
    keys = {'version', 'operation', 'rollback_deployment_id', 'rollback_payload_sha256',
            'expected_archive_commit', 'expected_v2_deployment_id', 'expected_v2_release_sha256'}
    if (not isinstance(plan, dict) or set(plan) != keys or type(plan['version']) is not int
            or plan['version'] != 1 or plan['operation'] != 'resume-v2-after-v1-rollback'):
        raise RecoveryError('reviewed V2 resume plan has an unexpected schema')
    for key in ('rollback_payload_sha256', 'expected_v2_release_sha256'):
        if not isinstance(plan[key], str) or not SHA64.fullmatch(plan[key]):
            raise RecoveryError('reviewed V2 resume plan has an invalid payload digest')
    for key in ('expected_archive_commit',):
        if not isinstance(plan[key], str) or not SHA40.fullmatch(plan[key]):
            raise RecoveryError('reviewed V2 resume plan needs exact Git identities')
    for key in ('rollback_deployment_id', 'expected_v2_deployment_id'):
        if type(plan[key]) is not int or plan[key] <= 0:
            raise RecoveryError('reviewed V2 resume plan needs exact deployment identities')
    return plan


def _resume_pair_key(release):
    release = _release(release, 2)
    return tuple(release[key] for key in (
        'main_commit', 'main_tree', 'archive_commit', 'archive_manifest_sha256',
        'served_manifest_sha256', 'site_sha256', 'probes'))


def _resume_recovery_authority(records, rollback_records, state):
    if records.main_commit() != state['main_commit']:
        raise RecoveryError('main changed during V2 resume')
    if records.archive_commit() != state['archive_head_to_preserve']:
        raise RecoveryError('archive head changed during V2 resume')
    rollback_row = rollback_records.rollback_latest()
    if (not rollback_row or rollback_row.get('id') != state['rollback_deployment_id']
            or rollback_row.get('payload') != state['rollback_payload']
            or _sha256(encode(rollback_row['payload'])) != state['rollback_payload_sha256']):
        raise RecoveryError('verified rollback point changed during V2 resume')
    if not rollback_records.is_verified(state['rollback_deployment_id']):
        raise RecoveryError('V1 rollback point is no longer verified')


def _resume_base(records, rollback_records, state):
    _resume_recovery_authority(records, rollback_records, state)
    verified = records.latest_verified()
    if (not verified or verified.get('id') != state['expected_v2_deployment_id']
            or verified.get('payload') != state['expected_v2_release']
            or _sha256(encode(verified['payload'])) != state['expected_v2_release_sha256']):
        raise RecoveryError('original V2 authority changed before resume completed')


def _resume_authority(records, rollback_records, state):
    _resume_base(records, rollback_records, state)
    latest = records.latest()
    if state['status'] == 'prepared':
        if (latest and latest.get('id') == state['predecessor_v2_deployment_id']
                and isinstance(latest.get('payload'), dict)
                and _sha256(encode(latest['payload'])) == state['predecessor_v2_release_sha256']):
            return
        if (not latest or _resume_pair_key(latest.get('payload')) != _resume_pair_key(state['release'])):
            raise RecoveryError('latest regular deployment is not the exact pending resume pair')
        return
    if (state['status'] not in {'recorded', 'authorized'}
            or not latest or latest.get('id') != state['resume_deployment_id']
            or latest.get('payload') != state['release']):
        raise RecoveryError('latest regular V2 deployment changed during resume')


def _validate_resume_state(state):
    keys = {'version', 'operation', 'status', 'site_url', 'captureless_stage', 'release',
            'release_sha256', 'pair_mode', 'main_commit', 'main_tree', 'run_id', 'run_attempt',
            'archive_head_to_preserve', 'rollback_deployment_id', 'rollback_payload',
            'rollback_payload_sha256', 'expected_v2_deployment_id', 'expected_v2_release',
            'expected_v2_release_sha256', 'predecessor_v2_deployment_id',
            'predecessor_v2_release_sha256', 'resume_deployment_id'}
    if (not isinstance(state, dict) or not keys <= set(state)
            or set(state) - keys - {'verification'} or type(state['version']) is not int
            or state['version'] != 1 or state['operation'] != 'resume-v2-after-v1-rollback'
            or state['status'] not in {'prepared', 'recorded', 'authorized', 'verified'}):
        raise RecoveryError('invalid V2 resume controller state')
    if ((state['status'] == 'verified') != ('verification' in state)
            or not isinstance(state['site_url'], str) or not state['site_url']
            or state['pair_mode'] not in {'exact-recorded-pair', 'superseding-current-code'}):
        raise RecoveryError('invalid V2 resume state status or context')
    release = _release(state['release'], 2)
    expected = _release(state['expected_v2_release'], 2)
    if (not SHA40.fullmatch(state['main_commit']) or not SHA40.fullmatch(state['main_tree'])
            or not SHA40.fullmatch(state['archive_head_to_preserve'])
            or state['archive_head_to_preserve'] != release['archive_commit']
            or state['main_commit'] != release['main_commit'] or state['main_tree'] != release['main_tree']
            or not isinstance(state['run_id'], str) or not re.fullmatch(r'[1-9][0-9]*', state['run_id'])
            or not isinstance(state['run_attempt'], str) or not re.fullmatch(r'[1-9][0-9]*', state['run_attempt'])
            or type(state['rollback_deployment_id']) is not int or state['rollback_deployment_id'] <= 0
            or type(state['expected_v2_deployment_id']) is not int or state['expected_v2_deployment_id'] <= 0
            or type(state['predecessor_v2_deployment_id']) is not int
            or state['predecessor_v2_deployment_id'] <= 0
            or not isinstance(state['rollback_payload_sha256'], str)
            or not SHA64.fullmatch(state['rollback_payload_sha256'])
            or not isinstance(state['expected_v2_release_sha256'], str)
            or not SHA64.fullmatch(state['expected_v2_release_sha256'])
            or not isinstance(state['predecessor_v2_release_sha256'], str)
            or not SHA64.fullmatch(state['predecessor_v2_release_sha256'])
            or state['expected_v2_release_sha256'] != _sha256(encode(expected))
            or not isinstance(state['release_sha256'], str)
            or not SHA64.fullmatch(state['release_sha256'])
            or state['release_sha256'] != _sha256(encode(release))
            or type(state['resume_deployment_id']) is not int and state['resume_deployment_id'] is not None
            or state['status'] == 'prepared' and state['resume_deployment_id'] is not None
            or state['status'] in {'recorded', 'authorized', 'verified'} and
               (type(state['resume_deployment_id']) is not int or state['resume_deployment_id'] <= 0)):
        raise RecoveryError('invalid V2 resume authority pins')
    _validate_rollback_payload(state['rollback_payload'])
    if (state['rollback_payload']['release']['version'] != 1
            or state['rollback_payload']['archive_head_to_preserve'] != state['archive_head_to_preserve']
            or state['rollback_payload']['v2_deployment_id'] != state['expected_v2_deployment_id']
            or state['rollback_payload']['v2_release'] != expected):
        raise RecoveryError('resume state does not match its original V2 and rollback authorities')
    return state


def prepare_resume(records, rollback_records, archive_loader, reviewed_plan, destination, *,
                   paused=False, main_commit=None, main_tree=None, run_id='1', run_attempt='1',
                   site_url=None, site_code_root=ROOT, make_release_fn=None, reconstruct_fn=None,
                   served_verifier=None):
    """Rebuild V2 from a pinned archive and current trusted code, with no source import."""
    _require_paused(paused)
    plan = validate_resume_plan(reviewed_plan)
    if (not isinstance(main_commit, str) or not SHA40.fullmatch(main_commit)
            or not isinstance(main_tree, str) or not SHA40.fullmatch(main_tree)
            or records.main_commit() != main_commit):
        raise RecoveryError('resume requires the exact current trusted main pair')
    if not isinstance(site_url, str) or not site_url:
        raise RecoveryError('V2 resume needs the Pages site URL')
    if not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'[1-9][0-9]*', run_attempt):
        raise RecoveryError('invalid V2 resume run identity')
    rollback_row = rollback_records.rollback_latest()
    if (not rollback_row or rollback_row.get('id') != plan['rollback_deployment_id']
            or _sha256(encode(rollback_row.get('payload'))) != plan['rollback_payload_sha256']):
        raise RecoveryError('reviewed plan does not identify the latest rollback point')
    rollback_payload = _validate_rollback_payload(rollback_row['payload'])
    if not rollback_records.is_verified(plan['rollback_deployment_id']):
        raise RecoveryError('reviewed rollback point is not verified')
    if (rollback_payload['archive_head_to_preserve'] != plan['expected_archive_commit']
            or rollback_payload['v2_deployment_id'] != plan['expected_v2_deployment_id']
            or rollback_payload['v2_release_sha256'] != plan['expected_v2_release_sha256']):
        raise RecoveryError('reviewed rollback point differs from resume plan')
    old_v2 = _release(rollback_payload['v2_release'], 2)
    if old_v2['archive_commit'] != plan['expected_archive_commit']:
        raise RecoveryError('rollback record does not preserve the reviewed archive')
    verified = records.latest_verified()
    if _sha256(encode(old_v2)) != plan['expected_v2_release_sha256']:
        raise RecoveryError('reviewed V2 release digest differs from rollback authority')
    if records.archive_commit() != plan['expected_archive_commit']:
        raise RecoveryError('preserved V2 archive head differs from reviewed resume plan')
    latest = records.latest()
    if not latest:
        raise RecoveryError('regular V2 deployment history is empty')
    predecessor_release = _release(latest.get('payload'), 2)
    if predecessor_release['archive_commit'] != plan['expected_archive_commit']:
        raise RecoveryError('latest regular V2 attempt does not preserve the reviewed archive')
    if (latest['id'] == plan['expected_v2_deployment_id']
            and predecessor_release != old_v2):
        raise RecoveryError('original V2 deployment payload differs from rollback authority')
    predecessor_id = latest['id']
    predecessor_digest = _sha256(encode(predecessor_release))
    original_verified = (verified and verified.get('id') == plan['expected_v2_deployment_id']
                        and verified.get('payload') == old_v2)
    completed_verified = (verified and verified.get('id') == latest['id']
                          and verified.get('payload') == predecessor_release
                          and predecessor_release['main_commit'] == main_commit
                          and predecessor_release['main_tree'] == main_tree)
    if (not original_verified and not completed_verified):
        raise RecoveryError('latest verified V2 authority differs from reviewed resume plan')

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('resume staging destination must not already exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.v2-resume-', dir=destination.parent))
    archive_folder, output_folder = temporary / 'archive', temporary / 'site'
    builder = make_release_fn or archive_release.make_release
    reconstructor = reconstruct_fn or archive_release.reconstruct
    previous_root, previous_output = build_site.ROOT, build_site.OUTPUT
    try:
        archive = archive_loader(plan['expected_archive_commit'], archive_folder)
        if isinstance(archive, tuple):
            archive = archive[0]
        if getattr(archive, 'commit', None) != plan['expected_archive_commit']:
            raise RecoveryError('archive loader returned a different commit')
        build_site.ROOT = Path(site_code_root)
        build_site.OUTPUT = output_folder
        if main_commit == old_v2['main_commit'] and main_tree == old_v2['main_tree']:
            release = reconstructor(old_v2, archive, main_commit, main_tree)
            pair_mode = 'exact-recorded-pair'
        else:
            release = builder(main_commit, main_tree, archive, run_id, run_attempt)
            pair_mode = 'superseding-current-code'
        release = _release(release, 2)
        if (release['main_commit'] != main_commit or release['main_tree'] != main_tree
                or release['archive_commit'] != plan['expected_archive_commit']
                or site_digest(output_folder) != release['site_sha256']
                or _sha256((output_folder / MANIFEST).read_bytes()) != release['served_manifest_sha256']):
            raise RecoveryError('rebuilt V2 site differs from its exact code/archive release')
        (output_folder / RELEASE).write_bytes(encode(release))
        pending = records.latest()
        if pending['id'] != predecessor_id:
            if (_sha256(encode(pending.get('payload'))) != predecessor_digest
                    and _resume_pair_key(pending.get('payload')) != _resume_pair_key(release)):
                raise RecoveryError('latest regular V2 authority changed during reconstruction')
        if pending['id'] != plan['expected_v2_deployment_id']:
            pending_release = _release(pending['payload'], 2)
            if _resume_pair_key(pending_release) == _resume_pair_key(release):
                # Reuse the durable pair, whether pending or already verified.
                release = pending_release
                (output_folder / RELEASE).write_bytes(encode(release))
            elif pending['id'] != predecessor_id or _sha256(encode(pending_release)) != predecessor_digest:
                raise RecoveryError('latest regular V2 authority changed during reconstruction')
        if site_digest(output_folder) != release['site_sha256']:
            raise RecoveryError('resume release metadata changed the rebuilt site')
    except Exception:
        import shutil
        shutil.rmtree(temporary, ignore_errors=True)
        build_site.ROOT, build_site.OUTPUT = previous_root, previous_output
        raise
    finally:
        build_site.ROOT, build_site.OUTPUT = previous_root, previous_output
    created_destination = False
    try:
        destination.mkdir()
        created_destination = True
        os.rename(output_folder, destination / 'site')
    except Exception:
        import shutil
        if created_destination:
            try:
                destination.rmdir()
            except OSError:
                pass
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    import shutil
    shutil.rmtree(temporary, ignore_errors=True)
    state = {
        'version': 1, 'operation': 'resume-v2-after-v1-rollback', 'status': 'prepared',
        'site_url': site_url, 'captureless_stage': str(destination.resolve()),
        'release': release, 'release_sha256': _sha256(encode(release)), 'pair_mode': pair_mode,
        'main_commit': main_commit, 'main_tree': main_tree, 'run_id': run_id, 'run_attempt': run_attempt,
        'archive_head_to_preserve': plan['expected_archive_commit'],
        'rollback_deployment_id': plan['rollback_deployment_id'], 'rollback_payload': rollback_payload,
        'rollback_payload_sha256': plan['rollback_payload_sha256'],
        'expected_v2_deployment_id': plan['expected_v2_deployment_id'], 'expected_v2_release': old_v2,
        'expected_v2_release_sha256': plan['expected_v2_release_sha256'],
        'predecessor_v2_deployment_id': predecessor_id,
        'predecessor_v2_release_sha256': predecessor_digest, 'resume_deployment_id': None,
    }
    latest = records.latest()
    latest_verified = records.latest_verified()
    if (latest and latest_verified and latest['id'] == latest_verified['id']
            and latest['id'] != plan['expected_v2_deployment_id']
            and latest.get('payload') == release):
        if not records.is_verified(latest['id']):
            raise RecoveryError('latest V2 completion status is no longer verified')
        _resume_recovery_authority(records, rollback_records, state)
        if served_verifier is None:
            from publish_site import served_release
            served_verifier = served_release
        result = served_verifier(site_url, release)
        _resume_recovery_authority(records, rollback_records, state)
        latest_after = records.latest()
        verified_after = records.latest_verified()
        if (not latest_after or latest_after.get('id') != latest['id']
                or latest_after.get('payload') != release
                or not verified_after or verified_after.get('id') != latest['id']
                or verified_after.get('payload') != release):
            raise RecoveryError('verified V2 completion authority changed during served verification')
        state['resume_deployment_id'] = latest['id']
        state['status'] = 'verified'
        state['verification'] = result
        return state
    _resume_authority(records, rollback_records, state)
    return state


def _resume_record_payload_matches(payload, release):
    try:
        return _resume_pair_key(payload) == _resume_pair_key(release)
    except RecoveryError:
        return False


def _verify_resume_stage(state):
    site = Path(state['captureless_stage']) / 'site'
    release_path = site / RELEASE
    if (site.is_symlink() or not site.is_dir() or release_path.is_symlink()
            or not release_path.is_file() or release_path.stat().st_size > 4096):
        raise RecoveryError('prepared V2 resume site is missing or unsafe')
    try:
        on_disk = json.loads(release_path.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, OSError) as error:
        raise RecoveryError('prepared V2 resume release metadata is invalid') from error
    if (on_disk != state['release'] or _sha256(encode(on_disk)) != state['release_sha256']
            or site_digest(site) != state['release']['site_sha256']):
        raise RecoveryError('prepared V2 resume site differs from its release')


def record_resume(records, rollback_records, state, *, paused=False, run_url=''):
    _require_paused(paused)
    state = _validate_resume_state(state)
    if state['status'] != 'prepared':
        raise RecoveryError('only a prepared V2 resume may be recorded')
    _verify_resume_stage(state)
    _resume_authority(records, rollback_records, state)
    latest = records.latest()
    if latest.get('id') == state['expected_v2_deployment_id']:
        identifier = records.create(state['release'])
    elif _resume_record_payload_matches(latest.get('payload'), state['release']):
        # Adopt the durable exact pair after an ambiguous create/deployment result.
        identifier = latest['id']
        state['release'] = latest['payload']
        state['release_sha256'] = _sha256(encode(state['release']))
        (Path(state['captureless_stage']) / 'site' / RELEASE).write_bytes(encode(state['release']))
    elif (latest.get('id') == state['predecessor_v2_deployment_id']
            and isinstance(latest.get('payload'), dict)
            and _sha256(encode(latest['payload'])) == state['predecessor_v2_release_sha256']):
        # Supersede only the exact unverified predecessor observed at prepare.
        identifier = records.create(state['release'])
    else:
        raise RecoveryError('latest regular V2 attempt differs from the prepared resume pair')
    _verify_resume_stage(state)
    state['resume_deployment_id'] = identifier
    state['status'] = 'recorded'
    records.status(identifier, 'in_progress', run_url)
    return state


def authorize_resume(records, rollback_records, state, *, paused=False):
    _require_paused(paused)
    state = _validate_resume_state(state)
    if state['status'] != 'recorded' or state['resume_deployment_id'] is None:
        raise RecoveryError('V2 resume must have a regular attempted release before deployment')
    _verify_resume_stage(state)
    _resume_authority(records, rollback_records, state)
    state['status'] = 'authorized'
    return state


def verify_resume(records, rollback_records, state, *, paused=False, run_url='', verifier=None):
    _require_paused(paused)
    state = _validate_resume_state(state)
    if state['status'] != 'authorized' or state['resume_deployment_id'] is None:
        raise RecoveryError('V2 resume deployment was not authorized')
    _verify_resume_stage(state)
    _resume_authority(records, rollback_records, state)
    if verifier is None:
        from publish_site import served_release
        verifier = served_release
    result = verifier(state['site_url'], state['release'])
    _verify_resume_stage(state)
    _resume_authority(records, rollback_records, state)
    records.status(state['resume_deployment_id'], 'success', run_url)
    state['status'] = 'verified'
    state['verification'] = result
    return state


def _read_json(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RecoveryError('rollback JSON file is missing, unsafe or oversized')
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, OSError) as error:
        raise RecoveryError('rollback JSON is invalid') from error


def _save_state(path, state):
    path = Path(path)
    payload = encode(state)
    if len(payload) > MAX_STATE_BYTES:
        raise RecoveryError('rollback state exceeds its size limit')
    if path.is_symlink():
        raise RecoveryError('rollback state path is unsafe')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    if temporary.exists() or temporary.is_symlink():
        raise RecoveryError('rollback state temporary path already exists')
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'record', 'authorize', 'verify',
                                            'resume-prepare', 'resume-record',
                                            'resume-authorize', 'resume-verify'))
    parser.add_argument('--paused', default=os.environ.get('DDD_PUBLICATION_PAUSED', 'false'))
    parser.add_argument('--main-commit', default=os.environ.get('MAIN_COMMIT'))
    parser.add_argument('--state', required=True)
    parser.add_argument('--site-url', default=os.environ.get('PAGES_URL'))
    parser.add_argument('--capture')
    parser.add_argument('--reviewed-plan')
    parser.add_argument('--stage')
    parser.add_argument('--run-url', default='')
    args = parser.parse_args(argv)
    if args.paused not in {'true', 'false'}:
        raise RecoveryError('DDD_PUBLICATION_PAUSED must be true or false')
    if os.environ.get('DDD_PUBLICATION_PAUSED') != 'true' or args.paused != 'true':
        raise RecoveryError('DDD_PUBLICATION_PAUSED must be true for every rollback command')
    if not args.main_commit or git_revision('HEAD') != args.main_commit:
        raise RecoveryError('rollback CLI must run from the exact current main checkout')
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    records = RollbackRecords(repository)
    regular_records = DeploymentRecords(repository)
    state_path = Path(args.state)
    if args.command in {'prepare', 'resume-prepare'}:
        if state_path.exists() or state_path.is_symlink():
            raise RecoveryError('prepare state destination must not already exist')
        if not args.reviewed_plan or not args.stage or not args.site_url:
            raise RecoveryError('prepare requires --reviewed-plan, --stage and --site-url')
        plan = _read_json(args.reviewed_plan, 16384)
        run_id = os.environ.get('GITHUB_RUN_ID', '1')
        run_attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        if args.command == 'prepare':
            if not args.capture:
                raise RecoveryError('prepare requires --capture')
            state = prepare(records, args.site_url, args.capture, plan, args.stage,
                            paused=args.paused == 'true', main_commit=args.main_commit,
                            run_id=run_id, run_attempt=run_attempt)
        else:
            main_tree = git_revision('HEAD^{tree}')
            if not worktree_clean():
                raise RecoveryError('V2 resume requires a clean trusted main worktree')

            def load_archive(commit, destination):
                return archive_git.read_remote_snapshot(repository, commit, destination)

            state = prepare_resume(regular_records, records, load_archive, plan, args.stage,
                                   paused=True, main_commit=args.main_commit, main_tree=main_tree,
                                   run_id=run_id, run_attempt=run_attempt, site_url=args.site_url)
    else:
        state = _read_json(state_path, MAX_STATE_BYTES)
        if (state.get('main_commit') != args.main_commit
                or state.get('run_id') != os.environ.get('GITHUB_RUN_ID')
                or state.get('run_attempt') != os.environ.get('GITHUB_RUN_ATTEMPT')):
            raise RecoveryError('rollback state belongs to different trusted code or workflow attempt')
        if args.command.startswith('resume-'):
            if state.get('main_tree') != git_revision('HEAD^{tree}') or not worktree_clean():
                raise RecoveryError('resume state belongs to different trusted code')
            state.setdefault('site_url', args.site_url)
            if args.site_url and state.get('site_url') != args.site_url:
                raise RecoveryError('resume site URL differs from prepared state')
        try:
            if args.command == 'record':
                state = record(records, state, paused=args.paused == 'true', run_url=args.run_url)
            elif args.command == 'authorize':
                state = authorize(records, state, paused=args.paused == 'true')
            elif args.command == 'verify':
                state = verify(records, state, paused=args.paused == 'true', run_url=args.run_url)
            elif args.command == 'resume-record':
                state = record_resume(regular_records, records, state,
                                      paused=args.paused == 'true', run_url=args.run_url)
            elif args.command == 'resume-authorize':
                state = authorize_resume(regular_records, records, state, paused=args.paused == 'true')
            else:
                state = verify_resume(regular_records, records, state,
                                      paused=args.paused == 'true', run_url=args.run_url)
        except Exception:
            # In particular, retain a created record ID if the status write
            # response is ambiguous; the next run must inspect history.
            if args.command == 'record' and state.get('deployment_id') is not None:
                _save_state(state_path, state)
            elif args.command == 'resume-record' and state.get('resume_deployment_id') is not None:
                _save_state(state_path, state)
            raise
    _save_state(state_path, state)
    if args.command == 'resume-prepare':
        complete = state['status'] == 'verified'
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write(f"resume_complete={'true' if complete else 'false'}\n")
        if complete and os.environ.get('GITHUB_STEP_SUMMARY'):
            summary = {
                'release': state['release'],
                'resume_deployment_id': state['resume_deployment_id'],
                'rollback_deployment_id': state['rollback_deployment_id'],
                'archive_head_preserved': state['archive_head_to_preserve'],
                'main_commit': state['main_commit'],
                'main_tree': state['main_tree'],
                'verification': state['verification'],
            }
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write('## V2 resume already verified\n\n')
                output.write('The exact current V2 pair was already verified and remains served; '
                             'the recovery workflow skipped deployment.\n\n')
                output.write('```json\n' + encode(summary).decode() + '\n```\n')
    print(encode(state).decode(), end='')


if __name__ == '__main__':
    try:
        main()
    except RecoveryError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
