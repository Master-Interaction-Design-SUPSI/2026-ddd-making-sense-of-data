"""Deterministic durable snapshots; archive data is never executable site code."""

import copy
import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES, select_content_files
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, validate_branch, validate_exclusions
from import_groups import validate_content
from publication_state import RecoveryError, decode_manifest, unique_keys

MANIFEST = 'publication.json'
STATES = frozenset({'live', 'frozen', 'hidden'})
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = MAX_SITE_BYTES + MAX_MANIFEST_BYTES + 16 * 1024 * 1024


class BoundedTarInfo(tarfile.TarInfo):
    """Bound extension headers before tarfile reads their attacker-declared size."""

    def _proc_member(self, stream):
        stream._archive_headers = getattr(stream, '_archive_headers', 0) + 1
        metadata = {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}
        if (stream._archive_headers > 40000 or self.size < 0 or self.size > MAX_TRANSFER_BYTES
                or self.type in metadata and self.size > 4096
                or self.type == tarfile.DIRTYPE and self.size != 0
                or self.type not in metadata | {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}):
            raise RecoveryError('unsafe or excessive archive header')
        return super()._proc_member(stream)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')


def blob_sha(content):
    return hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()


def decode_archive(payload):
    """Reject unknown schema/fields, unsafe paths and ambiguous provenance."""
    try:
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError('archive manifest exceeds its byte limit')
        manifest = json.loads(payload, object_pairs_hook=unique_keys)
        if (not isinstance(manifest, dict) or set(manifest) != {'version', 'groups'}
                or type(manifest['version']) is not int or manifest['version'] != 2
                or not isinstance(manifest['groups'], dict)):
            raise ValueError('expected archive manifest v2')
        total = 0
        for slug, entry in manifest['groups'].items():
            if not isinstance(slug, str) or len(slug) > 63 or not SLUG.fullmatch(slug) or slug in RESERVED_SLUGS:
                raise ValueError('invalid archive slug')
            if not isinstance(entry, dict) or set(entry) != {
                'repository', 'branch', 'state', 'require_visual', 'exclude', 'snapshot'
            }:
                raise ValueError(f'{slug}: invalid archive group record')
            if (not isinstance(entry['repository'], str) or not REPOSITORY.fullmatch(entry['repository'])
                    or entry['repository'].endswith('.git')):
                raise ValueError(f'{slug}: invalid repository')
            validate_branch(entry['branch'])
            validate_exclusions(entry['exclude'])
            if entry['state'] not in STATES or type(entry['require_visual']) is not bool:
                raise ValueError(f'{slug}: invalid state/settings')
            snapshot = entry['snapshot']
            if snapshot is None:
                if entry['state'] == 'frozen':
                    raise ValueError(f'{slug}: frozen group needs an accepted snapshot')
                continue
            if not isinstance(snapshot, dict) or set(snapshot) != {
                'commit', 'tree', 'contract_version', 'require_visual', 'exclude', 'files', 'excluded'
            }:
                raise ValueError(f'{slug}: invalid snapshot')
            if not isinstance(snapshot['commit'], str) or not SHA.fullmatch(snapshot['commit']):
                raise ValueError(f'{slug}: invalid source commit')
            if snapshot['tree'] is not None and (not isinstance(snapshot['tree'], str) or not SHA.fullmatch(snapshot['tree'])):
                raise ValueError(f'{slug}: invalid source tree')
            if type(snapshot['contract_version']) is not int or snapshot['contract_version'] not in {1, CONTRACT_VERSION}:
                raise ValueError(f'{slug}: unsupported contract')
            if type(snapshot['require_visual']) is not bool:
                raise ValueError(f'{slug}: invalid snapshot visual setting')
            validate_exclusions(snapshot['exclude'])
            files = snapshot['files']
            if not isinstance(files, dict):
                raise ValueError(f'{slug}: invalid file map')
            for name, record in files.items():
                if not isinstance(record, dict) or set(record) != {'size', 'sha256', 'blob_sha'}:
                    raise ValueError(f'{slug}: invalid file record')
                if (not isinstance(record['sha256'], str) or not DIGEST.fullmatch(record['sha256'])
                        or not isinstance(record['blob_sha'], str) or not SHA.fullmatch(record['blob_sha'])):
                    raise ValueError(f'{slug}: invalid file digest')
            selected = select_content_files({name: record['size'] for name, record in files.items()},
                                            require_visual=snapshot['require_visual'],
                                            recovery=snapshot['contract_version'] == 1)
            if set(selected) != set(files) or set(files) & set(snapshot['exclude']):
                raise ValueError(f'{slug}: unsupported/excluded archive content')
            if any('/'.join(name.split('/')[:i]) in files for name in files
                   for i in range(1, len(name.split('/')))):
                raise ValueError(f'{slug}: file/parent collision')
            excluded = snapshot['excluded']
            if not isinstance(excluded, dict) or set(excluded) != set(snapshot['exclude']):
                raise ValueError(f'{slug}: excluded provenance must identify every exclusion')
            if any(value is not None and (not isinstance(value, str) or not SHA.fullmatch(value))
                   for value in excluded.values()):
                raise ValueError(f'{slug}: invalid excluded blob identity')
            total += sum(record['size'] for record in files.values())
        # Hidden snapshots also need a bounded transfer. This is not a Pages size check.
        if total > MAX_SITE_BYTES:
            raise ValueError('archive snapshot exceeds bounded transfer limit')
        return manifest['groups']
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise RecoveryError(f'Invalid archive manifest: {error}') from error


def encode_archive(groups):
    payload = canonical({'version': 2, 'groups': groups})
    decode_archive(payload)
    return payload


def group_record(group, state=None):
    return {'repository': group.repository, 'branch': group.branch,
            'state': state or group.publication_state,
            'require_visual': group.require_visual, 'exclude': list(group.exclude), 'snapshot': None}


def seed_v1(payload, registry, read_file):
    """Seed from verified deployed bytes, requiring no surviving source repository."""
    published = decode_manifest(payload)
    configured = {group.slug: group for group in registry}
    groups = {group.slug: group_record(group) for group in registry}
    for slug, previous in published.items():
        group = configured.get(slug)
        if group and (group.repository.casefold(), group.branch) != (previous['repository'].casefold(), previous['branch']):
            raise RecoveryError(f'{slug}: seed repository identity differs from registry; migration required')
        if not group:
            groups[slug] = {'repository': previous['repository'], 'branch': previous['branch'], 'state': 'hidden',
                            'require_visual': previous.get('require_visual', True), 'exclude': [], 'snapshot': None}
        files = {}
        for name, record in previous['files'].items():
            content = read_file(slug, name)
            if len(content) != record['size'] or hashlib.sha256(content).hexdigest() != record['sha256']:
                raise RecoveryError(f'{slug}/{name}: seed bytes differ from verified V1 inventory')
            files[name] = record | {'blob_sha': blob_sha(content)}
        # V1 did not record exclusions or source trees. Unknown stays explicit.
        groups[slug]['snapshot'] = {'commit': previous['commit'], 'tree': None, 'contract_version': 1,
                                    'require_visual': previous.get('require_visual', True), 'exclude': [],
                                    'files': files, 'excluded': {}}
    encode_archive(groups)
    return groups


def reconcile(groups, registry, states=None):
    """Retain removed rows as hidden; require deliberate migrations for rebinds."""
    result = copy.deepcopy(groups)
    states = states or {}
    configured = {group.slug: group for group in registry}
    if set(states) - set(configured):
        raise RecoveryError('state override names an unregistered group')
    for slug, entry in result.items():
        if slug not in configured:
            entry['state'] = 'hidden'
    for slug, group in configured.items():
        entry = result.get(slug)
        if entry and (entry['repository'].casefold(), entry['branch']) != (group.repository.casefold(), group.branch):
            raise RecoveryError(f'{slug}: repository/branch rebind requires explicit migration')
        updated = group_record(group, states.get(slug))
        if entry:
            updated['snapshot'] = entry['snapshot']
        result[slug] = updated
    encode_archive(result)
    return result


def reconcile_v1_seed(previous, payload, registry, read_file):
    """Advance a pre-cutover seed while retaining disappeared snapshots hidden.

    This authority is restricted to V1 seeds. Once V2 has accepted any content,
    only normal archive transactions may advance the branch.
    """
    if any(entry['snapshot'] and (entry['snapshot']['contract_version'] != 1
                                  or entry['snapshot']['tree'] is not None)
           for entry in previous.values()):
        raise RecoveryError('seed reconciliation cannot replace V2 accepted content')
    # Reject identity rebinds even for a group absent from the new served release.
    retained = reconcile(previous, registry)
    current = seed_v1(payload, registry, read_file)
    published = decode_manifest(payload)
    for slug, entry in retained.items():
        if slug not in published and entry['snapshot']:
            current[slug] = copy.deepcopy(entry)
            current[slug]['state'] = 'hidden'
    encode_archive(current)
    return current


class ArchiveSnapshot:
    """Read a validated local snapshot from a pinned, bounded archive transfer."""

    def __init__(self, folder, commit):
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise RecoveryError('archive must be pinned to a full commit')
        self.folder = Path(folder)
        self.commit = commit
        if self.folder.is_symlink() or not self.folder.is_dir():
            raise RecoveryError('snapshot root must be a regular directory')
        manifest = self.folder / MANIFEST
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise RecoveryError('missing/unsafe archive manifest')
        self.payload = manifest.read_bytes()
        self.groups = decode_archive(self.payload)
        self.verify()

    def verify(self):
        expected = {MANIFEST} | {f'{slug}/{name}' for slug, entry in self.groups.items()
                                if entry['snapshot'] for name in entry['snapshot']['files']}
        actual = set()
        for path in self.folder.rglob('*'):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise RecoveryError('archive contains links or special files')
            if path.is_file():
                actual.add(path.relative_to(self.folder).as_posix())
        if actual != expected:
            raise RecoveryError('archive inventory differs from manifest')
        for slug, entry in self.groups.items():
            if not entry['snapshot']:
                continue
            for name in entry['snapshot']['files']:
                self.read_file(slug, name)
            try:
                validate_content(self.folder / slug, entry['snapshot']['files'],
                                 require_visual=entry['snapshot']['require_visual'])
            except ValueError as error:
                raise RecoveryError(f'{slug}: archive content is invalid: {error}') from error

    def read_file(self, slug, name):
        record = self.groups[slug]['snapshot']['files'][name]
        path = self.folder / slug / name
        if path.stat().st_size != record['size']:
            raise RecoveryError(f'{slug}/{name}: archive size mismatch')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record['sha256'] or blob_sha(content) != record['blob_sha']:
            raise RecoveryError(f'{slug}/{name}: archive digest mismatch')
        return content

    def served(self):
        """V1-compatible public inventory; hidden records never enter Pages."""
        groups = {}
        for slug, entry in self.groups.items():
            accepted = entry['snapshot']
            if entry['state'] == 'hidden' or accepted is None:
                continue
            groups[slug] = {'repository': entry['repository'], 'branch': entry['branch'],
                            'commit': accepted['commit'], 'require_visual': accepted['require_visual'],
                            'files': {name: {key: record[key] for key in ('size', 'sha256')}
                                      for name, record in accepted['files'].items()}}
        return groups


def unpack_snapshot(archive, destination, expected_digest, *, git_directories=False):
    """Validate all members before extraction; never trust tar extractall()."""
    archive, destination = Path(archive), Path(destination)
    if (not isinstance(expected_digest, str) or not DIGEST.fullmatch(expected_digest)
            or archive.is_symlink() or archive.stat().st_size > MAX_TRANSFER_BYTES):
        raise RecoveryError('invalid archive transport')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise RecoveryError('archive transport digest mismatch')
    if destination.exists():
        raise RecoveryError('snapshot destination must be new')
    with tarfile.open(archive, 'r:*', tarinfo=BoundedTarInfo) as stream:
        members, names, directories, total = [], set(), set(), 0
        for member in stream:
            if git_directories and member.isdir():
                name = member.name.rstrip('/')
                if (not name or name.startswith('/') or '\\' in name or len(name) > 576
                        or any(part in {'', '.', '..'} for part in name.split('/')) or name in directories):
                    raise RecoveryError('unsafe/duplicate archive directory')
                directories.add(name)
                if len(directories) > 20000:
                    raise RecoveryError('archive directory limit exceeded')
                continue
            path = PurePosixPath(member.name)
            if (not member.isfile() or member.name != path.as_posix() or path.is_absolute()
                    or any(part in {'', '.', '..'} for part in member.name.split('/'))
                    or '\\' in member.name or len(member.name) > 576 or member.name in names):
                raise RecoveryError('unsafe/duplicate archive member')
            if member.name != MANIFEST and (len(path.parts) < 2 or not SLUG.fullmatch(path.parts[0])
                                            or path.parts[0] in RESERVED_SLUGS):
                raise RecoveryError('unexpected archive member')
            names.add(member.name)
            total += member.size
            if total > MAX_SITE_BYTES + MAX_MANIFEST_BYTES or len(names) > 10000:
                raise RecoveryError('archive expanded size/member limit exceeded')
            members.append(member)
        if MANIFEST not in names:
            raise RecoveryError('archive has no manifest')
        manifest_member = next(member for member in members if member.name == MANIFEST)
        if manifest_member.size > MAX_MANIFEST_BYTES:
            raise RecoveryError('archive manifest exceeds byte limit')
        groups = decode_archive(stream.extractfile(manifest_member).read())
        expected = {MANIFEST} | {f'{slug}/{name}' for slug, entry in groups.items()
                                if entry['snapshot'] for name in entry['snapshot']['files']}
        if names != expected:
            raise RecoveryError('artifact inventory differs from manifest')
        parents = {'/'.join(name.split('/')[:index]) for name in expected
                   for index in range(1, len(name.split('/')))}
        if not directories <= parents:
            raise RecoveryError('unlisted archive directory')
        with tempfile.TemporaryDirectory(prefix='.archive-validate-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'snapshot'
            staged.mkdir()
            for member in members:
                path = staged / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, path.open('xb') as target:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            raise RecoveryError('truncated archive member')
                        target.write(chunk)
                        remaining -= len(chunk)
            # The transport digest alone cannot authorise bytes or active SVG.
            ArchiveSnapshot(staged, '0' * 40)
            staged.rename(destination)
    return groups
