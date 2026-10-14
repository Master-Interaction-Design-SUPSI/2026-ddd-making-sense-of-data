"""Prepare and independently admit archive candidates using trusted registry/code."""

from dataclasses import replace
import hashlib
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

import build_site
from archive_state import ArchiveSnapshot, blob_sha, encode_archive, reconcile, reconcile_v1_seed, seed_v1
from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES
from github_content import GitHubClient
from import_groups import import_groups
from publication_state import MANIFEST, RecoveryError, decode_manifest

RELEASE_RESERVE = 4096


def accepted_v1(entry):
    snapshot = entry['snapshot']
    if snapshot is None:
        return None
    return {'repository': entry['repository'], 'branch': entry['branch'], 'commit': snapshot['commit'],
            'require_visual': snapshot['require_visual'],
            'files': {name: {key: record[key] for key in ('size', 'sha256')}
                      for name, record in snapshot['files'].items()}}


class ArchiveBase:
    """Importer reuse adapter; each reused blob is checked against durable authority."""

    def __init__(self, archive):
        self.archive = archive
        self.groups = {slug: accepted_v1(entry) for slug, entry in archive.groups.items() if entry['snapshot']}
        self.deadline = float('inf')
        self.metrics = {'archive_read_bytes': 0}

    def read_file(self, slug, name):
        if time.monotonic() > self.deadline:
            raise RecoveryError('archive reuse deadline exceeded')
        content = self.archive.read_file(slug, name)
        self.metrics['archive_read_bytes'] += len(content)
        return content

    def reuse(self, slug, name, node):
        entry = self.archive.groups.get(slug)
        record = entry['snapshot']['files'].get(name) if entry and entry['snapshot'] else None
        if not record or record['blob_sha'] != node['sha'] or record['size'] != node['size']:
            return None
        # Corruption is a recovery failure, never an excuse to silently change authority.
        return self.read_file(slug, name)

    def can_skip(self, group, files):
        snapshot = self.archive.groups[group.slug]['snapshot']
        return (snapshot['contract_version'] == CONTRACT_VERSION
                and snapshot['exclude'] == list(group.exclude)
                and snapshot['require_visual'] == group.require_visual
                and {name: {'size': node['size'], 'blob_sha': node['sha']} for name, node in files.items()}
                    == {name: {key: record[key] for key in ('size', 'blob_sha')}
                        for name, record in snapshot['files'].items()})


def projected_site(folder):
    identity = build_site.build_archive(folder, '0' * 40)
    size = build_site.enforce_site_budget(build_site.OUTPUT)
    if size + RELEASE_RESERVE > MAX_SITE_BYTES:
        raise RecoveryError('projected visible site exceeds budget including release metadata')
    return identity | {'site_bytes': size}


def prepare_archive(registry, previous, destination, *, client_factory=GitHubClient, allow_unchanged=False,
                    allow_idle=False):
    """Validate live updates, preserve every accepted snapshot, then check site budget."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('archive preparation needs a new destination')
    groups = reconcile(previous.groups, registry)
    base = ArchiveBase(previous)
    with tempfile.TemporaryDirectory(prefix='.archive-prepare-', dir=destination.parent) as temporary:
        root = Path(temporary)
        imported = root / 'imports'
        live_registry = [replace(group, enabled=group.publication_state == 'live') for group in registry]
        results = import_groups(live_registry, imported, client_factory=client_factory, previous=base,
                                allow_unchanged=allow_unchanged)
        by_slug = {result['slug']: result for result in results}
        idle_eligible = (allow_idle
            and encode_archive(groups) == previous.payload
            and all(group.publication_state != 'live' or (
                groups[group.slug]['snapshot']
                and groups[group.slug]['snapshot']['contract_version'] == CONTRACT_VERSION
                and groups[group.slug]['snapshot']['tree'] is not None) for group in registry)
            and len(by_slug) == len(registry)
            and all(by_slug.get(group.slug, {}).get('status') ==
                    ('unchanged' if group.publication_state == 'live' else 'disabled')
                    for group in registry))
        if idle_eligible:
            for result in results:
                entry = groups[result['slug']]
                state = entry['state']
                result['publication_status'] = (state if state != 'live' and entry['snapshot'] else
                    'hidden' if state == 'hidden' else 'skipped' if state != 'live' else 'unchanged')
                selected = accepted_v1(entry)
                result['selected_source'] = ({key: selected[key] for key in ('repository', 'branch', 'commit')}
                                             if selected and state != 'hidden' else None)
            removed = [{'slug': slug, 'reason': 'Registry row removed; archived snapshot retained as hidden'}
                       for slug in previous.groups if slug not in {group.slug for group in registry}]
            return {'groups': results, 'removed_groups': removed, 'expected_parent': previous.commit,
                    'manifest_sha256': hashlib.sha256(previous.payload).hexdigest(),
                    'projected_site': None, 'idle': True, 'transport': base.metrics | {
                        key: sum(result.get('transport', {}).get(key, 0) for result in results)
                        for key in ('rest_requests', 'raw_requests', 'rest_bytes', 'raw_bytes')}}
        staged = root / 'snapshot'
        staged.mkdir()
        for slug, entry in groups.items():
            result = by_slug.get(slug)
            state = entry['state']
            if result and state == 'live' and result['status'] == 'imported':
                files = {}
                for name in result['files']:
                    content = (imported / slug / name).read_bytes()
                    files[name] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                                   'blob_sha': blob_sha(content)}
                entry['snapshot'] = {'commit': result['commit'], 'tree': result['tree'],
                    'contract_version': CONTRACT_VERSION, 'require_visual': entry['require_visual'],
                    'exclude': entry['exclude'], 'excluded': result['excluded_blobs'], 'files': files}
                shutil.copytree(imported / slug, staged / slug)
                result['publication_status'] = ('unchanged' if entry['snapshot'] == previous.groups.get(slug, {}).get('snapshot')
                                                else 'updated')
            elif entry['snapshot']:
                folder = staged / slug
                folder.mkdir()
                for name in entry['snapshot']['files']:
                    target = folder / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(previous.read_file(slug, name))
                if result:
                    result['publication_status'] = (state if state != 'live' else
                                                   'unchanged' if result['status'] == 'unchanged' else 'retained')
            elif result:
                result['publication_status'] = 'hidden' if state == 'hidden' else 'skipped'
            if result:
                selected = accepted_v1(entry)
                result['selected_source'] = ({key: selected[key] for key in ('repository', 'branch', 'commit')}
                                             if selected and state != 'hidden' else None)
        (staged / MANIFEST).write_bytes(encode_archive(groups))
        ArchiveSnapshot(staged, previous.commit)
        site = projected_site(staged)
        staged.rename(destination)
    removed = [{'slug': slug, 'reason': 'Registry row removed; archived snapshot retained as hidden'}
               for slug in previous.groups if slug not in {group.slug for group in registry}]
    return {'groups': results, 'removed_groups': removed, 'expected_parent': previous.commit,
            'manifest_sha256': hashlib.sha256((destination / MANIFEST).read_bytes()).hexdigest(),
            'projected_site': site, 'idle': False, 'transport': base.metrics | {
                key: sum(result.get('transport', {}).get(key, 0) for result in results)
                for key in ('rest_requests', 'raw_requests', 'rest_bytes', 'raw_bytes')}}


def admit_candidate(candidate, registry, previous=None, *, seed_manifest=None):
    """Writer-side policy validation; an artifact checksum is not authorization."""
    if previous is None:
        if seed_manifest is None:
            raise RecoveryError('orphan creation requires verified V1 seed authority')
        expected = seed_v1(seed_manifest, registry, candidate.read_file)
        if candidate.groups != expected:
            raise RecoveryError('seed candidate differs from verified V1/registry')
    elif seed_manifest is not None:
        expected = reconcile_v1_seed(previous.groups, seed_manifest, registry, candidate.read_file)
        if candidate.groups != expected:
            raise RecoveryError('reconciled seed differs from verified V1/retained parent')
        published = decode_manifest(seed_manifest)
        for slug, entry in previous.groups.items():
            if entry['snapshot'] and slug not in published:
                for name in entry['snapshot']['files']:
                    if candidate.read_file(slug, name) != previous.read_file(slug, name):
                        raise RecoveryError(f'{slug}: reconciled seed changed retained bytes')
    else:
        expected = reconcile(previous.groups, registry)
        if candidate.groups.keys() != expected.keys():
            raise RecoveryError('candidate added or removed an unapproved archive row')
        for slug, entry in candidate.groups.items():
            target = expected[slug]
            if {key: value for key, value in entry.items() if key != 'snapshot'} != {
                    key: value for key, value in target.items() if key != 'snapshot'}:
                raise RecoveryError(f'{slug}: candidate identity/state/settings differ from trusted registry')
            if entry['snapshot'] == target['snapshot']:
                continue
            if target['state'] != 'live' or entry['snapshot'] is None:
                raise RecoveryError(f'{slug}: candidate altered retained/frozen/hidden bytes')
            snapshot = entry['snapshot']
            if (snapshot['contract_version'] != CONTRACT_VERSION or snapshot['exclude'] != target['exclude']
                    or snapshot['require_visual'] != target['require_visual'] or snapshot['tree'] is None):
                raise RecoveryError(f'{slug}: new acceptance does not meet current policy')
    # Independently validated content, registry and whole-site budget precede any Git write.
    candidate.verify()
    return projected_site(candidate.folder)


def pack_snapshot(folder, output):
    """Deterministic regular-file transfer for immutable run/attempt artifacts."""
    snapshot = ArchiveSnapshot(folder, '0' * 40)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise RecoveryError('artifact destination must be new')
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
