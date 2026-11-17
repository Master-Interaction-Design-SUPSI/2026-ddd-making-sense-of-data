"""Prepare and independently admit archive candidates using trusted registry/code."""

import hashlib
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

import build_site
from archive_state import MAX_PROBLEM, ArchiveSnapshot, encode_archive, file_record, reconcile, served_entry
from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES
from github_content import GitHubClient, ImportFailure
from import_groups import import_groups
from release import MANIFEST, PublicationError

RELEASE_RESERVE = 4096


class ArchiveBase:
    """Importer reuse adapter; each reused blob is checked against durable authority."""

    def __init__(self, archive):
        self.archive = archive
        self.groups = {slug: served_entry(entry) for slug, entry in archive.groups.items() if entry['snapshot']}
        self.deadline = float('inf')
        self.metrics = {'archive_read_bytes': 0}

    def read_file(self, slug, name):
        if time.monotonic() > self.deadline:
            raise ImportFailure('timeout', 'Archive reuse exceeded the group time budget; retry later')
        content = self.archive.read_file(slug, name)
        self.metrics['archive_read_bytes'] += len(content)
        return content

    def reuse(self, slug, name, node):
        entry = self.archive.groups.get(slug)
        record = entry['snapshot']['files'].get(name) if entry and entry['snapshot'] else None
        if not record or record['blob_sha'] != node['sha'] or record['size'] != node['size']:
            return None
        # Corrupt archived bytes stop the run; they never silently change what was accepted.
        return self.read_file(slug, name)

    def problem(self, group):
        """The rejected newest commit recorded for this group's source, if any."""
        entry = self.archive.groups.get(group.slug)
        if entry and (entry['repository'].casefold(), entry['branch']) == (group.repository.casefold(), group.branch):
            return entry['problem']
        return None

    def can_skip(self, group, files):
        snapshot = self.archive.groups[group.slug]['snapshot']
        return (snapshot['exclude'] == list(group.exclude)
                and {name: {'size': node['size'], 'blob_sha': node['sha']} for name, node in files.items()}
                    == {name: {key: record[key] for key in ('size', 'blob_sha')}
                        for name, record in snapshot['files'].items()})


def projected_site(folder):
    build_site.build_archive(folder)
    if build_site.enforce_site_budget(build_site.OUTPUT) + RELEASE_RESERVE > MAX_SITE_BYTES:
        raise PublicationError('projected visible site exceeds budget including release metadata')


def publication_status(entry, previous, result):
    if not entry['snapshot']:
        return 'hidden' if entry['state'] == 'hidden' else 'skipped'
    if entry['state'] != 'live':
        return entry['state']
    if result['status'] == 'imported':
        return 'unchanged' if entry['snapshot'] == previous.get('snapshot') else 'updated'
    return 'unchanged' if result['status'] == 'unchanged' else 'retained'


def preparation_report(groups, previous, results, base, *, idle):
    for result in results:
        slug = result['slug']
        entry = groups[slug]
        result['publication_status'] = publication_status(entry, previous.groups.get(slug, {}), result)
        selected = served_entry(entry) if entry['state'] != 'hidden' else None
        result['selected_source'] = ({key: selected[key] for key in ('repository', 'branch', 'commit')}
                                     if selected else None)
    transport = base.metrics | {
        key: sum(result.get('transport', {}).get(key, 0) for result in results)
        for key in ('rest_requests', 'raw_requests', 'rest_bytes', 'raw_bytes')}
    return {'groups': results, 'idle': idle, 'transport': transport}


def prepare_archive(registry, previous, destination, *, client_factory=GitHubClient, allow_unchanged=False,
                    allow_idle=False):
    """Validate live updates, preserve every accepted snapshot, then check site budget."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise PublicationError('archive preparation needs a new destination')
    groups = reconcile(previous.groups, registry)
    base = ArchiveBase(previous)
    with tempfile.TemporaryDirectory(prefix='.archive-prepare-', dir=destination.parent) as temporary:
        root = Path(temporary)
        imported = root / 'imports'
        results = import_groups(registry, imported, client_factory=client_factory, previous=base,
                                allow_unchanged=allow_unchanged)
        by_slug = {result['slug']: result for result in results}
        # A known rejected commit is not imported again, so it keeps a run idle.
        idle_eligible = (allow_idle
            and encode_archive(groups) == previous.payload
            and all(group.state != 'live' or groups[group.slug]['snapshot'] or groups[group.slug]['problem']
                    for group in registry)
            and len(by_slug) == len(registry)
            and all(by_slug.get(group.slug, {}).get('status') in
                    ({'unchanged', 'rejected'} if group.state == 'live' else {'disabled'})
                    for group in registry))
        if idle_eligible:
            return preparation_report(groups, previous, results, base, idle=True)
        staged = root / 'snapshot'
        staged.mkdir()
        for slug, entry in groups.items():
            result = by_slug.get(slug)
            state = entry['state']
            if result and state == 'live' and result.get('error_code') == 'invalid_content' and result['commit']:
                # The newest commit was rejected for its content: the page says so; the snapshot stays.
                entry['problem'] = {'commit': result['commit'], 'message': result['error'][:MAX_PROBLEM]}
            if result and state == 'live' and result['status'] == 'imported':
                files = {name: file_record((imported / slug / name).read_bytes()) for name in result['files']}
                entry['snapshot'] = {'commit': result['commit'], 'tree': result['tree'],
                    'committed_at': result['committed_at'], 'contract_version': CONTRACT_VERSION,
                    'exclude': entry['exclude'], 'excluded': result['excluded_blobs'], 'files': files}
                entry['problem'] = None
                shutil.copytree(imported / slug, staged / slug)
            elif entry['snapshot']:
                folder = staged / slug
                folder.mkdir()
                for name in entry['snapshot']['files']:
                    target = folder / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(previous.read_file(slug, name))
        (staged / MANIFEST).write_bytes(encode_archive(groups))
        ArchiveSnapshot(staged)
        projected_site(staged)
        staged.rename(destination)
    return preparation_report(groups, previous, results, base, idle=False)


def admit_candidate(candidate, registry, previous):
    """Writer-side policy validation; an artifact checksum is not authorization.

    previous is the archive parent, or the empty archive on a first run.
    """
    expected = reconcile(previous.groups, registry)
    if candidate.groups.keys() != expected.keys():
        raise PublicationError('candidate added or removed an unapproved archive row')
    for slug, entry in candidate.groups.items():
        target = expected[slug]
        if {key: value for key, value in entry.items() if key not in {'snapshot', 'problem'}} != {
                key: value for key, value in target.items() if key not in {'snapshot', 'problem'}}:
            raise PublicationError(f'{slug}: candidate identity/state/settings differ from trusted registry')
        if entry['snapshot'] == target['snapshot']:
            continue
        if target['state'] != 'live' or entry['snapshot'] is None:
            raise PublicationError(f'{slug}: candidate altered retained/frozen/hidden bytes')
        snapshot = entry['snapshot']
        if snapshot['exclude'] != target['exclude']:
            raise PublicationError(f'{slug}: new acceptance does not meet current policy')
    # Independently validated content, registry and whole-site budget precede any Git write.
    candidate.verify()
    projected_site(candidate.folder)


def pack_snapshot(folder, output):
    """Deterministic regular-file transfer for immutable run/attempt artifacts."""
    snapshot = ArchiveSnapshot(folder)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise PublicationError('artifact destination must be new')
    with output.open('xb') as sink, tarfile.open(fileobj=sink, mode='w', format=tarfile.PAX_FORMAT) as stream:
        files = {MANIFEST: snapshot.payload}
        names = [f'{slug}/{name}' for slug, entry in snapshot.groups.items() if entry['snapshot']
                 for name in entry['snapshot']['files']]
        # Avoid retaining a cohort's binary bytes in memory.
        for name in sorted([MANIFEST] + names):
            if name == MANIFEST:
                content = files[MANIFEST]
            else:
                slug, relative = name.split('/', 1)
                content = snapshot.read_file(slug, relative)
            from io import BytesIO
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            stream.addfile(member, BytesIO(content))
    checksum = hashlib.sha256()
    with output.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            checksum.update(chunk)
    return checksum.hexdigest()
