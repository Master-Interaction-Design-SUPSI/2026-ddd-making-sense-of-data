"""Trusted split-job controller for request admission, durable acceptance and Pages."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import archive_git
import archive_release
import archive_rebind
import publish_site as legacy_publication
import build_site
from archive_state import ArchiveSnapshot, MAX_TRANSFER_BYTES, encode_archive, reconcile_v1_seed, seed_v1, unpack_snapshot
from archive_transaction import admit_candidate, pack_snapshot, prepare_archive
from content_contract import LIMITS
from deployment_records import DeploymentRecords
from github_content import ImportFailure
from group_registry import REPOSITORY, load_registry
from publication_requests import RequestAPI, body_hash, capture_batch, parse_group, refresh_allowed, validate_batch, answer_batch, LABEL
from publication_state import MANIFEST, MAX_MANIFEST_BYTES, PublishedSite, RecoveryError, unique_keys
from publish_site import RELEASE, encode, output, served_release, site_digest, validate_release

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.scratch/v2-build-state.json'
KINDS = {'admission', 'prepared', 'accepted', 'result'}
MODES = {'seed', 'reconcile_seed', 'rebind', 'refresh', 'unchanged', 'retry', 'repair', 'pending', 'deferred', 'invalid', 'superseded'}
MAX_REPORT_BYTES = 8 * 1024 * 1024
TRANSPORT_STAGE = ContextVar('publication_transport_stage', default=None)


def capture_transport(report):
    """Add only new calls since the last checkpoint in this controller invocation."""
    frame = TRANSPORT_STAGE.get()
    if frame is None:
        return
    stage = report.setdefault('transport', {}).setdefault(frame['name'] + '_api', {})
    for key, count in frame['client'].metrics.items():
        delta = count - frame['start'].get(key, 0)
        increment = delta - frame['applied'].get(key, 0)
        stage[key] = stage.get(key, 0) + increment
        frame['applied'][key] = delta


def transport_stage(name, *, durable_state=False):
    """Seal exclusive command costs with its artifact or same-job build state.

    Nested authorization during verification belongs to verification's budget.
    Separate CLI commands instantiate separate clients; their counters cannot be
    reconstructed from the final verify client alone.
    """
    def decorate(function):
        @wraps(function)
        def measured(records, *args, **kwargs):
            if TRANSPORT_STAGE.get() is not None:
                return function(records, *args, **kwargs)
            frame = {'name': name, 'client': records.client,
                     'start': dict(records.client.metrics), 'applied': {}, 'saved': False}
            token = TRANSPORT_STAGE.set(frame)
            completed = False
            try:
                result = function(records, *args, **kwargs)
                completed = True
                return result
            finally:
                try:
                    if durable_state and (completed or frame['saved']) and STATE.exists():
                        current = state()
                        _save(current)
                finally:
                    TRANSPORT_STAGE.reset(token)
        return measured
    return decorate


def sha_file(path, limit=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or limit is not None and path.stat().st_size > limit:
        raise RecoveryError('missing/unsafe/oversized job artifact file')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path, limit=MAX_MANIFEST_BYTES):
    sha_file(path, limit)
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique_keys)


def revision(ref):
    return subprocess.check_output(['git', 'rev-parse', ref], cwd=ROOT, text=True).strip()


def identity():
    repository = os.environ['GITHUB_REPOSITORY']
    main = os.environ['MAIN_COMMIT']
    if not REPOSITORY.fullmatch(repository) or not re.fullmatch(r'[0-9a-f]{40}', main) or revision('HEAD') != main:
        raise RecoveryError('job must execute the pinned trusted main checkout')
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    if not all(re.fullmatch(r'[1-9][0-9]*', value) for value in (run, attempt)):
        raise RecoveryError('invalid immutable run/attempt identity')
    return {'repository': repository, 'main_commit': main, 'main_tree': revision('HEAD^{tree}'),
            'run_id': run, 'run_attempt': attempt}


def require_main(records):
    context = identity()
    if records.main_commit() != context['main_commit']:
        raise RecoveryError('main changed; this run is superseded')
    return context


def registry():
    return load_registry(ROOT / 'groups.json')


def seal(folder, kind, data, files):
    context = identity()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'metadata.json').exists() or kind not in KINDS:
        raise RecoveryError('job artifact identity must be immutable')
    for name, source in files.items():
        if name not in {'batch.json', 'report.json', 'candidate.tar', 'seed-manifest.json'}:
            raise RecoveryError('unexpected job artifact member')
        target = folder / name
        if name == 'report.json' and isinstance(source, bytes):
            report = json.loads(source, object_pairs_hook=unique_keys)
            capture_transport(report)
            source = encode(report)
        if isinstance(source, bytes):
            target.write_bytes(source)
        elif Path(source) != target:
            shutil.copyfile(source, target)
    limits = {'candidate.tar': MAX_TRANSFER_BYTES, 'report.json': MAX_REPORT_BYTES}
    metadata = {'version': 1, 'kind': kind, **context,
                'registry_sha256': sha_file(ROOT / 'groups.json', MAX_MANIFEST_BYTES), 'data': data,
                'files': {name: sha_file(folder / name, limits.get(name, MAX_MANIFEST_BYTES)) for name in files}}
    (folder / 'metadata.json').write_bytes(encode(metadata))
    checksum = sha_file(folder / 'metadata.json', MAX_MANIFEST_BYTES)
    output('metadata_sha256', checksum)
    output('artifact_attempt', context['run_attempt'])
    output('mode', data['mode'])
    output('archive', 'true' if 'candidate.tar' in files else 'false')
    return checksum


def load(folder, expected_hash, kinds):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise RecoveryError('job artifact root must be a directory')
    if not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise RecoveryError('expected job artifact digest is missing')
    if sha_file(folder / 'metadata.json', MAX_MANIFEST_BYTES) != expected_hash:
        raise RecoveryError('job artifact metadata digest differs from trusted producing step')
    value = read_json(folder / 'metadata.json')
    context = identity()
    producer = os.environ.get('EXPECTED_PRODUCER_ATTEMPT', context['run_attempt'])
    if not re.fullmatch(r'[1-9][0-9]*', producer) or int(producer) > int(context['run_attempt']):
        raise RecoveryError('invalid explicit producing attempt')
    context['run_attempt'] = producer
    keys = {'version', 'kind', *context, 'registry_sha256', 'data', 'files'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1
            or value['kind'] not in kinds or any(value[key] != item for key, item in context.items())
            or value['registry_sha256'] != sha_file(ROOT / 'groups.json', MAX_MANIFEST_BYTES)
            or not isinstance(value['data'], dict) or value['data'].get('mode') not in MODES
            or not isinstance(value['files'], dict)
            or not {'batch.json', 'report.json'} <= value['files'].keys()
            or value['files'].keys() - {'batch.json', 'report.json', 'candidate.tar', 'seed-manifest.json'}):
        raise RecoveryError('job artifact code/registry/run/schema identity differs')
    if {path.name for path in folder.iterdir()} != {'metadata.json', *value['files']}:
        raise RecoveryError('job artifact contains unexpected files')
    for name, digest in value['files'].items():
        limit = MAX_TRANSFER_BYTES if name == 'candidate.tar' else MAX_REPORT_BYTES if name == 'report.json' else MAX_MANIFEST_BYTES
        if sha_file(folder / name, limit) != digest:
            raise RecoveryError('job artifact content changed: ' + name)
    batch = validate_batch(read_json(folder / 'batch.json'), context['repository'], registry())
    report = read_json(folder / 'report.json', MAX_REPORT_BYTES)
    if not isinstance(report, dict) or not isinstance(report.get('groups'), list):
        raise RecoveryError('invalid trusted group report')
    return value, batch, report


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class AdmissionRecords(DeploymentRecords):
    """A refresh reservation is operational timing, never accepted/published content."""

    def last_refresh(self):
        rows = json.loads(self.client.get(self.base + '/deployments?task=course-refresh&environment=course-refresh-cooldown&per_page=1', MAX_MANIFEST_BYTES))
        if not isinstance(rows, list) or len(rows) > 1:
            raise RecoveryError('invalid refresh reservation history')
        if not rows:
            return None
        row = rows[0]
        if row.get('task') != 'course-refresh' or row.get('environment') != 'course-refresh-cooldown':
            raise RecoveryError('unexpected refresh reservation')
        return row['created_at']

    def reserve(self, context, cutoff, event):
        return self.post('/deployments', {'ref': context['main_commit'], 'task': 'course-refresh',
            'environment': 'course-refresh-cooldown', 'auto_merge': False, 'required_contexts': [],
            'production_environment': False, 'description': 'Refresh admitted; this is not a publication',
            'payload': {'version': 1, **context, 'cutoff': cutoff, 'event': event}})


def admit(records, api, destination, event, *, issue_number=None, requests_enabled=False, seed=False,
          reconcile_seed=False, archive_enabled=False, rebind=False):
    context = require_main(records)
    cutoff = utc_now()
    groups = registry()
    mode = 'seed' if seed else 'reconcile_seed' if reconcile_seed else 'rebind' if rebind else 'refresh'
    if (event not in {'push', 'schedule', 'workflow_dispatch', 'issues'}
            or (seed or reconcile_seed or rebind) and event != 'workflow_dispatch'
            or sum(bool(value) for value in (seed, reconcile_seed, rebind)) > 1):
        raise RecoveryError('untrusted publication event/recovery input')
    if (seed or reconcile_seed) and archive_enabled:
        raise RecoveryError('seed operations are disabled after archive cutover')
    if rebind and not archive_enabled:
        raise RecoveryError('identity rebind requires archive authority after cutover')
    batch = {'version': 1, 'repository': context['repository'], 'cutoff': cutoff, 'requests': []}
    if event == 'issues':
        if not requests_enabled or type(issue_number) is not int or issue_number <= 0:
            mode = 'deferred'
        else:
            issue = api.get(f'/repos/{context["repository"]}/issues/{issue_number}')
            if (issue.get('number') != issue_number or issue.get('state') != 'open' or 'pull_request' in issue
                    or LABEL not in [row.get('name') for row in issue.get('labels', []) if isinstance(row, dict)]):
                mode = 'deferred'
            elif parse_group(issue.get('body') or '', groups) is None:
                mode = 'invalid'
                batch['requests'] = [{'number': issue_number, 'slug': None,
                                      'body_sha256': body_hash(issue.get('body') or '')}]
    if mode == 'refresh' and requests_enabled:
        batch = capture_batch(api, context['repository'], groups, cutoff)
    if mode == 'refresh' and not refresh_allowed(event, cutoff, records.last_refresh(), enabled=requests_enabled,
                                                api_remaining=records.client.api_remaining):
        mode = 'deferred'
    if mode == 'refresh':
        require_main(records)
        records.reserve(context, cutoff, event)
    return seal(destination, 'admission', {'mode': mode, 'cutoff': cutoff},
                {'batch.json': encode(batch), 'report.json': encode({'groups': [], 'transport': {
                    'admission': dict(records.client.metrics), 'issues': dict(api.client.metrics)}})})


def _read_archive(records, destination, commit=None):
    return archive_git.read_remote_snapshot(identity()['repository'], commit or records.archive_commit(), destination)


def _seed(records, url, destination, *, metrics=None, retained=None):
    if retained is not None and records.archive_authority(retained.commit) != 'v1-seed':
        raise RecoveryError('seed reconciliation cannot replace V2 archive authority')
    latest = records.latest()
    if not latest or validate_release(latest['payload'])['version'] != 1:
        raise RecoveryError('seed requires a currently verified V1 publication')
    release = latest['payload']
    if not records.is_verified(latest['id']):
        raise RecoveryError('seed requires the latest V1 deployment to be verified')
    served_release(url, release, metrics=metrics)
    previous = PublishedSite(url)
    if hashlib.sha256(previous.payload).hexdigest() != release['manifest_sha256']:
        raise RecoveryError('V1 advanced during seed capture; capture again')
    destination.mkdir()
    if any(sum(record['size'] for record in entry['files'].values()) > LIMITS['group_bytes']
           for entry in previous.groups.values()):
        raise RecoveryError('V1 snapshot exceeds new admission cap; explicit migration decision required')
    previous.overall_deadline = time.monotonic() + 20 * 60
    active_slug = None
    def read_seed(slug, name):
        nonlocal active_slug
        if slug != active_slug:
            previous.deadline = time.monotonic() + 180
            active_slug = slug
        return previous.read_file(slug, name)
    # Validation and materialization read the same verified bytes. Keep this
    # disposable acceleration store outside the admitted snapshot inventory.
    with tempfile.TemporaryDirectory(prefix='ddd-v1-seed-cache-', dir=destination.parent) as cache:
        previous.cache_directory = Path(cache)
        try:
            groups = (seed_v1(previous.payload, registry(), read_seed) if retained is None
                      else reconcile_v1_seed(retained.groups, previous.payload, registry(), read_seed))
            for slug, entry in groups.items():
                if entry['snapshot']:
                    folder = destination / slug
                    folder.mkdir()
                    for name in entry['snapshot']['files']:
                        target = folder / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        content = (read_seed(slug, name) if slug in previous.groups
                                   else retained.read_file(slug, name))
                        target.write_bytes(content)
            (destination / MANIFEST).write_bytes(encode_archive(groups))
            previous.check_unchanged()
        finally:
            if metrics is not None:
                for key, count in previous.metrics.items():
                    metrics[key] = metrics.get(key, 0) + count
    return ArchiveSnapshot(destination, '0' * 40), previous.payload, release


@transport_stage('prepare')
def prepare(records, url, admission, checksum, destination):
    context = require_main(records)
    admitted, batch, report = load(admission, checksum, {'admission'})
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    mode = admitted['data']['mode']
    data = {'mode': mode, 'archive_commit': None, 'expected_parent': None, 'previous_release': None,
            'deployment_id': None, 'supersedes': None, 'seed_release': None, 'source_checked': False}
    files = {'batch.json': encode(batch)}
    if mode in {'deferred', 'invalid', 'superseded'}:
        files['report.json'] = encode(report)
        return seal(destination, 'prepared', data, files)
    with tempfile.TemporaryDirectory(prefix='ddd-v2-prepare-') as temporary:
        root = Path(temporary)
        if mode == 'rebind':
            spec = read_json(ROOT / '.github/archive-rebind.json', 16384)
            if not isinstance(spec, dict):
                raise RecoveryError('reviewed rebind plan must be a bounded object')
            latest = records.latest()
            if (not latest or validate_release(latest['payload'])['version'] != 2
                    or not records.is_verified(latest['id'])):
                raise RecoveryError('rebind requires a verified V2 release; recover pending publication first')
            previous, metrics = _read_archive(records, root / 'previous')
            if previous.commit != latest['payload']['archive_commit']:
                raise RecoveryError('archive is ahead of verified publication; recover before identity rebind')
            if records.archive_authority(previous.commit) != 'v2':
                raise RecoveryError('identity rebind requires durable V2 archive authority')
            report['transport']['archive'] = metrics
            data['expected_parent'] = previous.commit
            data['previous_release'] = latest['payload']
            snapshot, derived = archive_rebind.prepare_rebind(previous, registry(), spec.get('slug'),
                spec.get('old_repository'), spec.get('old_branch'), root / 'rebind')
            if spec != derived:
                raise RecoveryError('reviewed rebind plan differs from current parent/registry')
            archive_rebind.admit_rebind(snapshot, registry(), previous, spec)
            report['migration'] = spec
        elif mode in {'seed', 'reconcile_seed'}:
            previous = None
            if mode == 'seed':
                try:
                    records.archive_commit()
                except ImportFailure as error:
                    if error.code != 'unavailable_repository':
                        raise
                else:
                    raise RecoveryError('archive already exists; inspect and reconcile instead of reseeding')
            else:
                previous, metrics = _read_archive(records, root / 'previous')
                report['transport']['archive'] = metrics
                data['expected_parent'] = previous.commit
            report['transport']['seed_pages'] = {}
            snapshot, manifest, release = _seed(records, url, root / 'seed',
                                                metrics=report['transport']['seed_pages'], retained=previous)
            admit_candidate(snapshot, registry(), previous, seed_manifest=manifest)
            data['seed_release'] = release
            files['seed-manifest.json'] = manifest
            report['groups'] = []
        else:
            latest = records.latest()
            # Classifying this exact attempt needs one status request, not a history sweep.
            verified = latest if latest and records.is_verified(latest['id']) else None
            strategy = archive_release.publication_strategy(latest, verified, context['main_commit'])
            data['supersedes'] = strategy.get('supersedes')
            if strategy['mode'] == 'verify_then_retry':
                release = strategy['release']
                data.update(mode='retry', archive_commit=release['archive_commit'],
                            previous_release=release, deployment_id=strategy['deployment_id'])
                try:
                    served_release(url, release, attempts=1)
                except RecoveryError:
                    pass
                else:
                    data['mode'] = 'repair'
                files['report.json'] = encode(report)
                return seal(destination, 'prepared', data, files)
            previous, metrics = _read_archive(records, root / 'previous')
            report['transport']['archive'] = metrics
            data['expected_parent'] = previous.commit
            data['previous_release'] = latest['payload'] if latest else None
            if strategy['mode'] == 'migration':
                if records.archive_authority(previous.commit) == 'v2':
                    accepted_main = records.archive_main(previous.commit)
                    if accepted_main == context['main_commit']:
                        data.update(mode='pending', archive_commit=previous.commit)
                        files['report.json'] = encode(report)
                        return seal(destination, 'prepared', data, files)
                    data['supersedes'] = latest['id']
                else:
                    served = served_release(url, latest['payload'])
                    normalized = {slug: entry | {'require_visual': entry.get('require_visual', True)}
                                  for slug, entry in served.items()}
                    if previous.served() != normalized:
                        raise RecoveryError('seed is stale or differs from verified V1; reconcile before cutover')
            elif latest and latest['payload']['main_commit'] == context['main_commit'] and previous.commit != latest['payload']['archive_commit']:
                # Acceptance survived, but its build/record job did not. Publish it first.
                data.update(mode='pending', archive_commit=previous.commit)
                files['report.json'] = encode(report)
                return seal(destination, 'prepared', data, files)
            result = prepare_archive(registry(), previous, root / 'candidate',
                                     allow_unchanged=bool(latest and latest['payload']['version'] == 2
                                         and latest['payload']['main_commit'] == context['main_commit']),
                                     allow_idle=bool(verified and verified['payload']['version'] == 2
                                         and verified['payload']['main_commit'] == context['main_commit']
                                         and verified['payload']['main_tree'] == context['main_tree']
                                         and verified['payload']['archive_commit'] == previous.commit
                                         and legacy_publication.worktree_clean()))
            report['groups'] = result['groups']
            report['removed_groups'] = result['removed_groups']
            report['transport']['import'] = result['transport']
            data['source_checked'] = True
            if result.get('idle'):
                data.update(mode='unchanged', archive_commit=previous.commit)
                files['report.json'] = encode(report)
                return seal(destination, 'prepared', data, files)
            snapshot = ArchiveSnapshot(root / 'candidate', previous.commit)
            if (snapshot.payload == previous.payload and latest and latest['payload']['version'] == 2
                    and latest['payload']['main_commit'] == context['main_commit']
                    and site_digest(build_site.OUTPUT) == latest['payload']['site_sha256']):
                data.update(mode='unchanged', archive_commit=previous.commit)
                files['report.json'] = encode(report)
                return seal(destination, 'prepared', data, files)
        pack_snapshot(snapshot.folder, destination / 'candidate.tar')
        files['candidate.tar'] = destination / 'candidate.tar'
        files['report.json'] = encode(report)
    return seal(destination, 'prepared', data, files)


@transport_stage('accept')
def accept(records, url, preparation, checksum, destination, *, writes_enabled=False, archive_enabled=False):
    context = require_main(records)
    if not writes_enabled:
        raise RecoveryError('archive writes are disabled until protection/pilot approval')
    prepared, batch, report = load(preparation, checksum, {'prepared'})
    data = prepared['data']
    if data['mode'] not in {'seed', 'reconcile_seed', 'rebind', 'refresh'} or 'candidate.tar' not in prepared['files']:
        raise RecoveryError('only an independently admitted candidate may be accepted')
    seed_operation = data['mode'] in {'seed', 'reconcile_seed'}
    rebind_operation = data['mode'] == 'rebind'
    if seed_operation and archive_enabled:
        raise RecoveryError('seed operations are disabled after archive cutover')
    if (data['mode'] == 'seed' and data['expected_parent'] is not None
            or data['mode'] == 'reconcile_seed' and data['expected_parent'] is None):
        raise RecoveryError('seed mode does not match expected archive parent')
    if rebind_operation and (not archive_enabled or data['expected_parent'] is None):
        raise RecoveryError('identity rebind requires archive authority and an exact parent')
    with tempfile.TemporaryDirectory(prefix='ddd-v2-writer-') as temporary:
        root = Path(temporary)
        unpack_snapshot(Path(preparation) / 'candidate.tar', root / 'candidate', prepared['files']['candidate.tar'])
        candidate = ArchiveSnapshot(root / 'candidate', data['expected_parent'] or '0' * 40)
        git = archive_git.init_remote(root / 'writer.git', context['repository'])
        archive_git.require_heads(git, context['main_commit'], data['expected_parent'])
        seed_payload = None
        if data['mode'] in {'seed', 'reconcile_seed'}:
            release = validate_release(data['seed_release'])
            latest = records.latest()
            if (release['version'] != 1 or not latest or latest['payload'] != release
                    or not records.is_verified(latest['id'])):
                raise RecoveryError('seed authority differs from latest verified V1')
            served_release(url, release)
            seed_payload = (Path(preparation) / 'seed-manifest.json').read_bytes()
            if hashlib.sha256(seed_payload).hexdigest() != release['manifest_sha256']:
                raise RecoveryError('seed authority differs from current V1')
        if data['expected_parent']:
            archive_git.fetch_commit(git, data['expected_parent'])
            if seed_operation and archive_git.accepted_authority(git, data['expected_parent']) != 'v1-seed':
                raise RecoveryError('seed reconciliation cannot replace V2 archive authority')
            previous = archive_git.snapshot_from_git(git, data['expected_parent'], root / 'previous')
            if rebind_operation:
                latest = records.latest()
                if (not latest or latest['payload'] != data['previous_release']
                        or validate_release(latest['payload'])['version'] != 2
                        or not records.is_verified(latest['id'])
                        or latest['payload']['archive_commit'] != data['expected_parent']
                        or archive_git.accepted_authority(git, data['expected_parent']) != 'v2'):
                    raise RecoveryError('rebind authority changed; recover or prepare the current parent')
                archive_rebind.admit_rebind(candidate, registry(), previous,
                                            read_json(ROOT / '.github/archive-rebind.json', 16384))
            else:
                admit_candidate(candidate, registry(), previous, seed_manifest=seed_payload)
        else:
            admit_candidate(candidate, registry(), seed_manifest=seed_payload)
        commit = archive_git.create_commit(git, candidate.folder, data['expected_parent'],
                                           main_commit=context['main_commit'],
                                           authority='v1-seed' if seed_operation else 'v2')
        archive_git.publish_commit(git, commit, context['main_commit'], data['expected_parent'], authenticated=True)
    data['archive_commit'] = commit
    output('archive_commit', commit)
    return seal(destination, 'accepted', data, {'batch.json': encode(batch), 'report.json': encode(report)})


def _save(state):
    capture_transport(state['report'])
    if TRANSPORT_STAGE.get() is not None:
        TRANSPORT_STAGE.get()['saved'] = True
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_bytes(encode(state))


@transport_stage('build', durable_state=True)
def build(records, url, source, checksum):
    context = require_main(records)
    payload, batch, report = load(source, checksum, {'prepared', 'accepted'})
    data = payload['data']
    state = {'identity': context, 'data': data, 'status': 'building', 'url': url, 'release': None,
             'deployment_id': data['deployment_id'], 'batch': batch, 'report': report}
    _save(state)
    output('deploy', 'false')
    try:
        if data['mode'] in {'invalid', 'deferred', 'superseded', 'seed', 'reconcile_seed', 'rebind'}:
            state['status'] = data['mode']
            return
        if data['mode'] == 'refresh' and payload['kind'] != 'accepted':
            raise RecoveryError('unarchived candidate content cannot be deployed')
        if data['mode'] in {'refresh', 'pending'}:
            latest = records.latest()
            previous = data['previous_release']
            if latest and latest['payload'] != previous:
                attempted = validate_release(latest['payload'])
                if (attempted['version'] == 2 and attempted['main_commit'] == context['main_commit']
                        and attempted['archive_commit'] == data['archive_commit']):
                    # An earlier build attempt recorded this accepted pair before failing.
                    data.update(mode='retry', previous_release=attempted, deployment_id=latest['id'])
                    state['deployment_id'] = latest['id']
                else:
                    raise RecoveryError('another release superseded this accepted run')
            elif records.archive_commit() != data['archive_commit']:
                raise RecoveryError('archive advanced; this accepted run is superseded')
        if data['mode'] in {'repair', 'unchanged'}:
            state['release'] = validate_release(data['previous_release'])
            served_release(url, state['release'])
            if data['mode'] == 'repair':
                latest = records.latest()
                if not latest or latest['id'] != data['deployment_id'] or latest['payload'] != state['release']:
                    raise RecoveryError('pending record changed during verification repair')
                records.status(data['deployment_id'], 'success', _run_url())
            state['status'] = 'verified' if data['mode'] == 'unchanged' else 'recovered'
            return
        with tempfile.TemporaryDirectory(prefix='ddd-v2-build-') as temporary:
            archive, metrics = _read_archive(records, Path(temporary) / 'archive', data['archive_commit'])
            report['transport']['build_archive'] = metrics
            if data['mode'] == 'retry':
                release = archive_release.reconstruct(data['previous_release'], archive, context['main_commit'], context['main_tree'])
            else:
                release = archive_release.make_release(context['main_commit'], context['main_tree'], archive,
                                                       context['run_id'], context['run_attempt'])
        state.update(status='prepared', release=release)
        output('deploy', 'true')
    except Exception as error:
        state.update(status='failed', error=str(error))
        raise
    finally:
        _save(state)


def _run_url():
    context = identity()
    return f'https://github.com/{context["repository"]}/actions/runs/{context["run_id"]}/attempts/{context["run_attempt"]}'


def state():
    value = read_json(STATE, MAX_REPORT_BYTES + MAX_MANIFEST_BYTES)
    if value['identity'] != identity():
        raise RecoveryError('build state belongs to different code/run')
    return value


@transport_stage('record', durable_state=True)
def record(records):
    require_main(records)
    current = state()
    release = validate_release(current['release'])
    if (current['status'] != 'prepared' or site_digest(build_site.OUTPUT) != release['site_sha256']
            or read_json(build_site.OUTPUT / RELEASE, 4096) != release):
        raise RecoveryError('candidate changed or was not prepared')
    if current['data']['mode'] == 'retry':
        latest = records.latest()
        if not latest or latest['id'] != current['deployment_id'] or latest['payload'] != release:
            raise RecoveryError('retry no longer names the latest attempted release')
    else:
        current['deployment_id'] = records.create(release)
    records.status(current['deployment_id'], 'in_progress', _run_url())
    current['status'] = 'awaiting_deployment'
    _save(current)
    output('deploy', 'true')


@transport_stage('authorize', durable_state=True)
def authorize(records):
    require_main(records)
    current = state()
    latest = records.latest()
    if (current['status'] != 'awaiting_deployment' or not latest or latest['id'] != current['deployment_id']
            or latest['payload'] != current['release']):
        raise RecoveryError('deployment identity changed before Pages mutation')
    output('deploy', 'true')


@transport_stage('verify', durable_state=True)
def verify(records):
    authorize(records)
    current = state()
    try:
        served_release(current['url'], current['release'])
        authorize(records)
        records.status(current['deployment_id'], 'success', _run_url())
        current['status'] = 'verified' if current['data']['source_checked'] else 'recovered'
    except Exception as error:
        current.update(status='unverified', error=str(error))
        raise
    finally:
        _save(current)


def finalize(source, checksum, destination):
    payload, batch, report = load(source, checksum, {'prepared', 'accepted'})
    current = state() if STATE.exists() else {'status': 'failed', 'report': report, 'batch': batch,
                                           'data': payload['data'], 'release': None, 'error': 'Build stage did not complete'}
    current['data']['stage'] = current['status']
    current['data']['release'] = current['release']
    if current.get('error'):
        current['report']['error'] = current['error']
    return seal(destination, 'result', current['data'],
                {'batch.json': encode(current['batch']), 'report.json': encode(current['report'])})


def answer(api, source, checksum, records=None):
    context = identity()
    if records is not None and records.main_commit() != context['main_commit']:
        return
    payload, batch, report = load(source, checksum, {'admission', 'prepared', 'accepted', 'result'})
    mode = payload['data']['mode']
    stage = payload['data'].get('stage', mode if mode in {'deferred', 'invalid', 'superseded'} else 'failed')
    # Recovery publishes previously accepted content; it did not check new source requests.
    if stage == 'recovered':
        stage = 'deferred'
    answer_batch(api, batch, context['repository'], registry(), report['groups'], stage,
                 int(context['run_id']), int(context['run_attempt']), os.environ['PAGES_URL'])
    print(encode({'issue_transport': api.client.metrics}).decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('admit', 'prepare', 'accept', 'build', 'record', 'authorize', 'verify', 'finalize', 'answer'))
    args = parser.parse_args()
    context = identity()
    source = ROOT / '.scratch/v2-input'
    checksum = os.environ.get('EXPECTED_METADATA_SHA256', '')
    destination = ROOT / '.scratch/v2-output'
    url = os.environ.get('PAGES_URL', '')
    if args.command == 'admit':
        event = read_json(os.environ['GITHUB_EVENT_PATH'])
        admit(AdmissionRecords(context['repository']), RequestAPI(context['repository']), destination,
              os.environ['GITHUB_EVENT_NAME'], issue_number=event.get('issue', {}).get('number'),
              requests_enabled=os.environ.get('REQUESTS_ENABLED') == 'true',
              seed=os.environ.get('SEED_ARCHIVE') == 'true',
              reconcile_seed=os.environ.get('RECONCILE_SEED') == 'true',
              archive_enabled=os.environ.get('ARCHIVE_ENABLED') == 'true',
              rebind=os.environ.get('REBIND_ARCHIVE') == 'true')
    elif args.command == 'prepare':
        prepare(DeploymentRecords(context['repository']), url, source, checksum, destination)
    elif args.command == 'accept':
        accept(DeploymentRecords(context['repository']), url, source, checksum, destination,
                writes_enabled=os.environ.get('ARCHIVE_WRITES_ENABLED') == 'true',
                archive_enabled=os.environ.get('ARCHIVE_ENABLED') == 'true')
    elif args.command == 'build':
        build(DeploymentRecords(context['repository']), url, source, checksum)
    elif args.command == 'finalize':
        finalize(source, checksum, destination)
    elif args.command == 'answer':
        answer(RequestAPI(context['repository']), source, checksum, DeploymentRecords(context['repository']))
    else:
        {'record': record, 'authorize': authorize, 'verify': verify}[args.command](DeploymentRecords(context['repository']))


if __name__ == '__main__':
    main()
