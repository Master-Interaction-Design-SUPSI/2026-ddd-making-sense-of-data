"""Deterministic durable snapshots; archive data is never executable site code."""

import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES, select_content_files
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, STATES, validate_branch, validate_exclusions
from import_groups import validate_content
from release import PublicationError, unique_keys

MANIFEST = 'publication.json'
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
MAX_ARCHIVE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = MAX_SITE_BYTES + MAX_ARCHIVE_MANIFEST_BYTES + 16 * 1024 * 1024


class BoundedTarInfo(tarfile.TarInfo):
    """Bound extension headers before tarfile reads their attacker-declared size."""

    def _proc_member(self, stream):
        stream._archive_headers = getattr(stream, '_archive_headers', 0) + 1
        metadata = {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}
        if (stream._archive_headers > 40000 or self.size < 0 or self.size > MAX_TRANSFER_BYTES
                or self.type in metadata and self.size > 4096
                or self.type == tarfile.DIRTYPE and self.size != 0
                or self.type not in metadata | {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}):
            raise PublicationError('unsafe or excessive archive header')
        return super()._proc_member(stream)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')


def blob_sha(content):
    return hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()


def file_record(content):
    return {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'blob_sha': blob_sha(content)}


def served_groups(groups):
    """Public inventory for the served manifest; hidden records never enter Pages."""
    return {slug: served_entry(entry) for slug, entry in groups.items()
            if entry['state'] != 'hidden' and entry['snapshot']}


def served_entry(entry):
    """One group's record in the served manifest, or None before its first accepted snapshot."""
    snapshot = entry['snapshot']
    if snapshot is None:
        return None
    return {'repository': entry['repository'], 'branch': entry['branch'], 'commit': snapshot['commit'],
            'files': {name: {key: record[key] for key in ('size', 'sha256')}
                      for name, record in snapshot['files'].items()}}


def decode_archive(payload):
    """Reject unknown schema/fields, unsafe paths and ambiguous provenance."""
    try:
        if len(payload) > MAX_ARCHIVE_MANIFEST_BYTES:
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
                'repository', 'branch', 'state', 'exclude', 'snapshot'
            }:
                raise ValueError(f'{slug}: invalid archive group record')
            if (not isinstance(entry['repository'], str) or not REPOSITORY.fullmatch(entry['repository'])
                    or entry['repository'].endswith('.git')):
                raise ValueError(f'{slug}: invalid repository')
            validate_branch(entry['branch'])
            validate_exclusions(entry['exclude'])
            if entry['state'] not in STATES:
                raise ValueError(f'{slug}: invalid state')
            snapshot = entry['snapshot']
            if snapshot is None:
                if entry['state'] == 'frozen':
                    raise ValueError(f'{slug}: frozen group needs an accepted snapshot')
                continue
            if not isinstance(snapshot, dict) or set(snapshot) != {
                'commit', 'tree', 'contract_version', 'exclude', 'files', 'excluded'
            }:
                raise ValueError(f'{slug}: invalid snapshot')
            if not isinstance(snapshot['commit'], str) or not SHA.fullmatch(snapshot['commit']):
                raise ValueError(f'{slug}: invalid source commit')
            if not isinstance(snapshot['tree'], str) or not SHA.fullmatch(snapshot['tree']):
                raise ValueError(f'{slug}: invalid source tree')
            if type(snapshot['contract_version']) is not int or snapshot['contract_version'] != CONTRACT_VERSION:
                raise ValueError(f'{slug}: unsupported contract')
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
            selected = select_content_files({name: record['size'] for name, record in files.items()})
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
        raise PublicationError(f'Invalid archive manifest: {error}') from error


def encode_archive(groups):
    payload = canonical({'version': 2, 'groups': groups})
    decode_archive(payload)
    return payload


def group_record(group):
    return {'repository': group.repository, 'branch': group.branch, 'state': group.state,
            'exclude': list(group.exclude), 'snapshot': None}


def reconcile(groups, registry):
    """The archive mirrors the registry: a row per group, keeping a snapshot only for the same source."""
    result = {}
    for group in registry:
        entry = groups.get(group.slug)
        result[group.slug] = group_record(group)
        if entry and (entry['repository'].casefold(), entry['branch']) == (group.repository.casefold(), group.branch):
            result[group.slug]['snapshot'] = entry['snapshot']
        elif entry and group.state == 'frozen':
            raise PublicationError(f'{group.slug}: a frozen group keeps its accepted snapshot; change its repository '
                                'or branch only while it is live or hidden')
    encode_archive(result)
    return result


class ArchiveSnapshot:
    """Read a validated local snapshot from a pinned, bounded archive transfer."""

    def __init__(self, folder, commit=None):
        # commit is set only for a snapshot read from student-work.
        if commit is not None and (not isinstance(commit, str) or not SHA.fullmatch(commit)):
            raise PublicationError('archive must be pinned to a full commit')
        self.folder = Path(folder)
        self.commit = commit
        if self.folder.is_symlink() or not self.folder.is_dir():
            raise PublicationError('snapshot root must be a regular directory')
        manifest = self.folder / MANIFEST
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_ARCHIVE_MANIFEST_BYTES:
            raise PublicationError('missing/unsafe archive manifest')
        self.payload = manifest.read_bytes()
        self.groups = decode_archive(self.payload)
        self.verify()

    def verify(self):
        expected = {MANIFEST} | {f'{slug}/{name}' for slug, entry in self.groups.items()
                                if entry['snapshot'] for name in entry['snapshot']['files']}
        actual = set()
        for path in self.folder.rglob('*'):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise PublicationError('archive contains links or special files')
            if path.is_file():
                actual.add(path.relative_to(self.folder).as_posix())
        if actual != expected:
            raise PublicationError('archive inventory differs from manifest')
        for slug, entry in self.groups.items():
            if not entry['snapshot']:
                continue
            for name in entry['snapshot']['files']:
                self.read_file(slug, name)
            try:
                validate_content(self.folder / slug, entry['snapshot']['files'])
            except ValueError as error:
                raise PublicationError(f'{slug}: archive content is invalid: {error}') from error

    def read_file(self, slug, name):
        record = self.groups[slug]['snapshot']['files'][name]
        path = self.folder / slug / name
        if path.stat().st_size != record['size']:
            raise PublicationError(f'{slug}/{name}: archive size mismatch')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record['sha256'] or blob_sha(content) != record['blob_sha']:
            raise PublicationError(f'{slug}/{name}: archive digest mismatch')
        return content

    def served(self):
        return served_groups(self.groups)


def empty_archive(folder):
    """The archive of a first run: no student-work branch yet, so no groups and no commit."""
    folder = Path(folder)
    folder.mkdir()
    (folder / MANIFEST).write_bytes(encode_archive({}))
    return ArchiveSnapshot(folder)


def unpack_snapshot(archive, destination, expected_digest, *, git_directories=False):
    """Validate all members before extraction; never trust tar extractall()."""
    archive, destination = Path(archive), Path(destination)
    if (not isinstance(expected_digest, str) or not DIGEST.fullmatch(expected_digest)
            or archive.is_symlink() or archive.stat().st_size > MAX_TRANSFER_BYTES):
        raise PublicationError('invalid archive transport')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise PublicationError('archive transport digest mismatch')
    if destination.exists():
        raise PublicationError('snapshot destination must be new')
    with tarfile.open(archive, 'r:*', tarinfo=BoundedTarInfo) as stream:
        members, names, directories, total = [], set(), set(), 0
        for member in stream:
            if git_directories and member.isdir():
                name = member.name.rstrip('/')
                if (not name or name.startswith('/') or '\\' in name or len(name) > 576
                        or any(part in {'', '.', '..'} for part in name.split('/')) or name in directories):
                    raise PublicationError('unsafe/duplicate archive directory')
                directories.add(name)
                if len(directories) > 20000:
                    raise PublicationError('archive directory limit exceeded')
                continue
            path = PurePosixPath(member.name)
            if (not member.isfile() or member.name != path.as_posix() or path.is_absolute()
                    or any(part in {'', '.', '..'} for part in member.name.split('/'))
                    or '\\' in member.name or len(member.name) > 576 or member.name in names):
                raise PublicationError('unsafe/duplicate archive member')
            if member.name != MANIFEST and (len(path.parts) < 2 or not SLUG.fullmatch(path.parts[0])
                                            or path.parts[0] in RESERVED_SLUGS):
                raise PublicationError('unexpected archive member')
            names.add(member.name)
            total += member.size
            if total > MAX_SITE_BYTES + MAX_ARCHIVE_MANIFEST_BYTES or len(names) > 10000:
                raise PublicationError('archive expanded size/member limit exceeded')
            members.append(member)
        if MANIFEST not in names:
            raise PublicationError('archive has no manifest')
        manifest_member = next(member for member in members if member.name == MANIFEST)
        if manifest_member.size > MAX_ARCHIVE_MANIFEST_BYTES:
            raise PublicationError('archive manifest exceeds byte limit')
        groups = decode_archive(stream.extractfile(manifest_member).read())
        expected = {MANIFEST} | {f'{slug}/{name}' for slug, entry in groups.items()
                                if entry['snapshot'] for name in entry['snapshot']['files']}
        if names != expected:
            raise PublicationError('artifact inventory differs from manifest')
        parents = {'/'.join(name.split('/')[:index]) for name in expected
                   for index in range(1, len(name.split('/')))}
        if not directories <= parents:
            raise PublicationError('unlisted archive directory')
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
                            raise PublicationError('truncated archive member')
                        target.write(chunk)
                        remaining -= len(chunk)
            # The transport digest alone cannot authorise bytes or active SVG.
            ArchiveSnapshot(staged)
            staged.rename(destination)
    return groups
