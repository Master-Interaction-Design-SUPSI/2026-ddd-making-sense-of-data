"""Trusted split-job controller for request admission, durable acceptance and Pages."""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import archive_git
import archive_release
import build_site
from archive_state import MANIFEST, ArchiveSnapshot, MAX_TRANSFER_BYTES, empty_archive, unpack_snapshot
from archive_transaction import admit_candidate, pack_snapshot, prepare_archive
from deployment_records import DeploymentRecords
from group_registry import REPOSITORY, load_registry
from publication_requests import RequestAPI, body_hash, capture_batch, parse_group, refresh_allowed, validate_batch, answer_batch, LABEL
from release import (MAX_MANIFEST_BYTES, RELEASE, PublicationError, encode, output, require_publication_active,
                     served_release, site_digest, unique_keys, validate_release, worktree_clean)

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.scratch/build-state.json'
KINDS = {'admission', 'prepared', 'accepted', 'result'}
MODES = {'refresh', 'unchanged', 'pending', 'retry', 'repair', 'deferred', 'invalid', 'superseded'}
MEMBERS = {'batch.json', 'report.json', 'candidate.tar'}
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
        raise PublicationError('missing/unsafe/oversized job artifact file')
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
        raise PublicationError('job must execute the pinned trusted main checkout')
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    if not all(re.fullmatch(r'[1-9][0-9]*', value) for value in (run, attempt)):
        raise PublicationError('invalid immutable run/attempt identity')
    return {'repository': repository, 'main_commit': main, 'main_tree': revision('HEAD^{tree}'),
            'run_id': run, 'run_attempt': attempt}


def require_main(records):
    require_publication_active()
    context = identity()
    if records.main_commit() != context['main_commit']:
        raise PublicationError('main changed; this run is superseded')
    return context


def registry():
    return load_registry(ROOT / 'groups.json')


def seal(folder, kind, data, files):
    context = identity()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'metadata.json').exists() or kind not in KINDS:
        raise PublicationError('job artifact identity must be immutable')
    for name, source in files.items():
        if name not in MEMBERS:
            raise PublicationError('unexpected job artifact member')
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
    # The next job downloads this artifact by name and admits it only with this exact metadata digest.
    artifact = f"{kind}-{context['run_id']}-{context['run_attempt']}"
    output('artifact', artifact)
    output('state', f'{artifact}:{checksum}')
    output('mode', data['mode'])
    output('archive', 'true' if 'candidate.tar' in files else 'false')
    return checksum


def load(folder, expected_hash, kinds):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise PublicationError('job artifact root must be a directory')
    if not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise PublicationError('expected job artifact digest is missing')
    if sha_file(folder / 'metadata.json', MAX_MANIFEST_BYTES) != expected_hash:
        raise PublicationError('job artifact metadata digest differs from trusted producing step')
    value = read_json(folder / 'metadata.json')
    context = identity()
    producer = os.environ.get('EXPECTED_PRODUCER_ATTEMPT', context['run_attempt'])
    if not re.fullmatch(r'[1-9][0-9]*', producer) or int(producer) > int(context['run_attempt']):
        raise PublicationError('invalid explicit producing attempt')
    context['run_attempt'] = producer
    keys = {'version', 'kind', *context, 'registry_sha256', 'data', 'files'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1
            or value['kind'] not in kinds or any(value[key] != item for key, item in context.items())
            or value['registry_sha256'] != sha_file(ROOT / 'groups.json', MAX_MANIFEST_BYTES)
            or not isinstance(value['data'], dict) or value['data'].get('mode') not in MODES
            or not isinstance(value['files'], dict)
            or not {'batch.json', 'report.json'} <= value['files'].keys()
            or value['files'].keys() - MEMBERS):
        raise PublicationError('job artifact code/registry/run/schema identity differs')
    if {path.name for path in folder.iterdir()} != {'metadata.json', *value['files']}:
        raise PublicationError('job artifact contains unexpected files')
    for name, digest in value['files'].items():
        limit = MAX_TRANSFER_BYTES if name == 'candidate.tar' else MAX_REPORT_BYTES if name == 'report.json' else MAX_MANIFEST_BYTES
        if sha_file(folder / name, limit) != digest:
            raise PublicationError('job artifact content changed: ' + name)
    batch = validate_batch(read_json(folder / 'batch.json'), context['repository'], registry())
    report = read_json(folder / 'report.json', MAX_REPORT_BYTES)
    if not isinstance(report, dict) or not isinstance(report.get('groups'), list):
        raise PublicationError('invalid trusted group report')
    return value, batch, report


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class AdmissionRecords(DeploymentRecords):
    """A refresh reservation is operational timing, never accepted/published content."""

    def last_refresh(self):
        rows = json.loads(self.client.get(self.base + '/deployments?task=course-refresh&environment=course-refresh-cooldown&per_page=1', MAX_MANIFEST_BYTES))
        if not isinstance(rows, list) or len(rows) > 1:
            raise PublicationError('invalid refresh reservation history')
        if not rows:
            return None
        row = rows[0]
        if row.get('task') != 'course-refresh' or row.get('environment') != 'course-refresh-cooldown':
            raise PublicationError('unexpected refresh reservation')
        return row['created_at']

    def reserve(self, context, cutoff, event):
        return self.post('/deployments', {'ref': context['main_commit'], 'task': 'course-refresh',
            'environment': 'course-refresh-cooldown', 'auto_merge': False, 'required_contexts': [],
            'production_environment': False, 'description': 'Refresh admitted; this is not a publication',
            'payload': {'version': 1, **context, 'cutoff': cutoff, 'event': event}})


def admit(records, api, destination, event, *, issue_number=None, requests_enabled=False):
    context = require_main(records)
    cutoff = utc_now()
    groups = registry()
    mode = 'refresh'
    if event not in {'push', 'schedule', 'workflow_dispatch', 'issues'}:
        raise PublicationError('untrusted publication event')
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


def _read_archive(destination, commit):
    """Read the accepted archive at commit, or start from the empty archive on a first run."""
    if commit is None:
        return empty_archive(destination), {'archive_transfer_bytes': 0}
    return archive_git.read_remote_snapshot(identity()['repository'], commit, destination)


@transport_stage('prepare')
def prepare(records, url, admission, checksum, destination):
    context = require_main(records)
    admitted, batch, report = load(admission, checksum, {'admission'})
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    data = {'mode': admitted['data']['mode'], 'archive_commit': None, 'expected_parent': None,
            'previous_release': None, 'deployment_id': None, 'source_checked': False}
    files = {'batch.json': encode(batch)}

    def finish(**changes):
        data.update(changes)
        files['report.json'] = encode(report)
        return seal(destination, 'prepared', data, files)

    if data['mode'] in {'deferred', 'invalid', 'superseded'}:
        return finish()
    latest = records.latest()
    # Classifying this exact attempt needs one status request, not a history sweep.
    verified = latest if latest and records.is_verified(latest['id']) else None
    strategy = archive_release.publication_strategy(latest, verified, context['main_commit'])
    if strategy['mode'] == 'verify_then_retry':
        attempted = strategy['release']
        try:
            served_release(url, attempted, attempts=1)
            mode = 'repair'
        except PublicationError:
            mode = 'retry'
        return finish(mode=mode, archive_commit=attempted['archive_commit'], previous_release=attempted,
                      deployment_id=strategy['deployment_id'])
    head = records.archive_commit()
    if latest and head is None:
        raise PublicationError('a course-publication deployment record exists but the student-work branch is '
                            'missing; restore the branch before publishing')
    same_main = bool(latest and latest['payload']['main_commit'] == context['main_commit'])
    with tempfile.TemporaryDirectory(prefix='ddd-prepare-') as temporary:
        root = Path(temporary)
        # No branch and no record: the first run starts from the empty archive.
        previous, report['transport']['archive'] = _read_archive(root / 'previous', head)
        data.update(expected_parent=previous.commit, previous_release=latest['payload'] if latest else None)
        if same_main and previous.commit != latest['payload']['archive_commit']:
            # Acceptance survived, but its build/record job did not. Publish it first.
            return finish(mode='pending', archive_commit=previous.commit)
        result = prepare_archive(registry(), previous, root / 'candidate', allow_unchanged=same_main,
                                 allow_idle=bool(same_main and verified
                                     and verified['payload']['main_tree'] == context['main_tree']
                                     and verified['payload']['archive_commit'] == previous.commit
                                     and worktree_clean()))
        report['groups'] = result['groups']
        report['transport']['import'] = result['transport']
        data['source_checked'] = True
        if result['idle'] or (same_main and (root / 'candidate' / MANIFEST).read_bytes() == previous.payload
                              and site_digest(build_site.OUTPUT) == latest['payload']['site_sha256']):
            return finish(mode='unchanged', archive_commit=previous.commit)
        pack_snapshot(root / 'candidate', destination / 'candidate.tar')
        files['candidate.tar'] = destination / 'candidate.tar'
        return finish()


@transport_stage('accept')
def accept(records, preparation, checksum, destination):
    context = require_main(records)
    prepared, batch, report = load(preparation, checksum, {'prepared'})
    data = prepared['data']
    if data['mode'] != 'refresh' or 'candidate.tar' not in prepared['files']:
        raise PublicationError('only an independently admitted candidate may be accepted')
    with tempfile.TemporaryDirectory(prefix='ddd-writer-') as temporary:
        root = Path(temporary)
        unpack_snapshot(Path(preparation) / 'candidate.tar', root / 'candidate', prepared['files']['candidate.tar'])
        candidate = ArchiveSnapshot(root / 'candidate')
        git = archive_git.init_remote(root / 'writer.git', context['repository'])
        # A first run expects no student-work branch, so a concurrent first run cannot be replaced.
        archive_git.require_heads(git, context['main_commit'], data['expected_parent'])
        previous = archive_git.parent_snapshot(git, data['expected_parent'], root / 'previous')
        admit_candidate(candidate, registry(), previous)
        commit = archive_git.create_commit(git, candidate.folder, data['expected_parent'],
                                           main_commit=context['main_commit'])
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
        if data['mode'] in {'invalid', 'deferred', 'superseded'}:
            state['status'] = data['mode']
            return
        if data['mode'] == 'refresh' and payload['kind'] != 'accepted':
            raise PublicationError('unarchived candidate content cannot be deployed')
        latest = None
        if data['mode'] in {'refresh', 'pending'}:
            latest = records.latest()
            previous = data['previous_release']
            if latest and latest['payload'] != previous:
                attempted = validate_release(latest['payload'])
                if (attempted['main_commit'] == context['main_commit']
                        and attempted['archive_commit'] == data['archive_commit']):
                    # An earlier build attempt recorded this accepted pair before failing.
                    data.update(mode='retry', previous_release=attempted, deployment_id=latest['id'])
                    state['deployment_id'] = latest['id']
                else:
                    raise PublicationError('another release superseded this accepted run')
            elif (head := records.archive_commit()) != data['archive_commit']:
                raise PublicationError('the student-work branch is missing' if head is None
                                    else 'archive advanced; this accepted run is superseded')
        if data['mode'] in {'repair', 'unchanged'}:
            state['release'] = validate_release(data['previous_release'])
            served_release(url, state['release'])
            if data['mode'] == 'repair':
                latest = records.latest()
                if not latest or latest['id'] != data['deployment_id'] or latest['payload'] != state['release']:
                    raise PublicationError('pending record changed during verification repair')
                records.status(data['deployment_id'], 'success', _run_url())
            state['status'] = 'verified' if data['mode'] == 'unchanged' else 'recovered'
            return
        with tempfile.TemporaryDirectory(prefix='ddd-build-') as temporary:
            archive, metrics = _read_archive(Path(temporary) / 'archive', data['archive_commit'])
            report['transport']['build_archive'] = metrics
            if data['mode'] == 'retry':
                release = archive_release.reconstruct(data['previous_release'], archive, context['main_commit'], context['main_tree'])
            else:
                previous_groups = archive_release.verified_previous_groups(
                    records, context['repository'], records.latest_verified())
                release = archive_release.make_release(context['main_commit'], context['main_tree'], archive,
                                                       context['run_id'], context['run_attempt'],
                                                       previous_groups=previous_groups)
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
        raise PublicationError('build state belongs to different code/run')
    return value


@transport_stage('record', durable_state=True)
def record(records):
    require_main(records)
    current = state()
    release = validate_release(current['release'])
    if (current['status'] != 'prepared' or site_digest(build_site.OUTPUT) != release['site_sha256']
            or read_json(build_site.OUTPUT / RELEASE, 4096) != release):
        raise PublicationError('candidate changed or was not prepared')
    if current['data']['mode'] == 'retry':
        latest = records.latest()
        if not latest or latest['id'] != current['deployment_id'] or latest['payload'] != release:
            raise PublicationError('retry no longer names the latest attempted release')
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
        raise PublicationError('deployment identity changed before Pages mutation')
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
    require_publication_active()
    context = identity()
    if records is not None and records.main_commit() != context['main_commit']:
        return
    payload, batch, report = load(source, checksum, {'admission', 'prepared', 'accepted', 'result'})
    mode = payload['data']['mode']
    stage = payload['data'].get('stage', mode if mode in {'deferred', 'invalid', 'superseded'} else 'failed')
    # A retry or repair publishes previously accepted content; it did not check new source requests.
    if stage == 'recovered':
        stage = 'deferred'
    answer_batch(api, batch, context['repository'], registry(), report['groups'], stage,
                 int(context['run_id']), int(context['run_attempt']), os.environ['PAGES_URL'])
    print(encode({'issue_transport': api.client.metrics}).decode())


def summary():
    """The maintainers' "Course publication" step summary: one row per group. Students read their entry."""
    try:
        current = state()
    except (PublicationError, OSError, ValueError, KeyError):
        current = None

    def cell(value):
        escaped = html.escape(str(value)).replace('\n', ' ').replace('\r', ' ')
        for character in '|[]`*_\\':
            escaped = escaped.replace(character, f'&#{ord(character)};')
        return escaped

    lines = ['## Course publication', '']
    if current is None:
        lines.append('Build stage did not complete; see the failed job.')
    else:
        lines += ['Result: **' + cell(current['status']) + '**.', '',
                  '| Group | Status | Online | Attempted | Detail |', '| --- | --- | --- | --- | --- |']
        for result in current['report'].get('groups', []):
            online = (result.get('selected_source') or {}).get('commit')
            detail = result.get('error') or '; '.join(result.get('warnings', []))
            values = (result['slug'], result.get('publication_status', result['status']),
                      online[:7] if online else '—', (result.get('commit') or '—')[:7], detail)
            lines.append('| ' + ' | '.join(cell(value) for value in values) + ' |')
        if current.get('error'):
            lines += ['', cell(current['error'])]
    text = '\n'.join(lines) + '\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(text)


def input_state():
    """Read INPUT_STATE, '<kind>-<run>-<attempt>:<metadata sha256>', sealed by the job that ran before.

    Returns the digest; the attempt pins the producing attempt for load(), so a rerun of a later
    job can use an artifact from an earlier successful attempt of the same run.
    """
    artifact, _, checksum = os.environ.get('INPUT_STATE', '').partition(':')
    if artifact:
        os.environ['EXPECTED_PRODUCER_ATTEMPT'] = artifact.rsplit('-', 1)[-1]
    return checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('admit', 'prepare', 'accept', 'build', 'record', 'authorize', 'verify',
                                            'finalize', 'answer', 'summary'))
    args = parser.parse_args()
    context = identity()
    source = ROOT / '.scratch/job-input'
    checksum = input_state()
    destination = ROOT / '.scratch/job-output'
    url = os.environ.get('PAGES_URL', '')
    if args.command == 'admit':
        event = read_json(os.environ['GITHUB_EVENT_PATH'])
        admit(AdmissionRecords(context['repository']), RequestAPI(context['repository']), destination,
              os.environ['GITHUB_EVENT_NAME'], issue_number=event.get('issue', {}).get('number'),
              requests_enabled=os.environ.get('REQUESTS_ENABLED') == 'true')
    elif args.command == 'prepare':
        prepare(DeploymentRecords(context['repository']), url, source, checksum, destination)
    elif args.command == 'accept':
        accept(DeploymentRecords(context['repository']), source, checksum, destination)
    elif args.command == 'build':
        build(DeploymentRecords(context['repository']), url, source, checksum)
    elif args.command == 'finalize':
        finalize(source, checksum, destination)
    elif args.command == 'summary':
        summary()
    elif args.command == 'answer':
        answer(RequestAPI(context['repository']), source, checksum, DeploymentRecords(context['repository']))
    else:
        {'record': record, 'authorize': authorize, 'verify': verify}[args.command](DeploymentRecords(context['repository']))


if __name__ == '__main__':
    main()
