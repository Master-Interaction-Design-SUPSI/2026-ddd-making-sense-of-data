"""Git plumbing for expected-parent archive writes, without contributor checkout."""

import os
import subprocess
import tempfile
import time
import base64
from pathlib import Path

from archive_state import ArchiveSnapshot, SHA, MAX_MANIFEST_BYTES, MAX_TRANSFER_BYTES, decode_archive, empty_archive, unpack_snapshot
from group_registry import REPOSITORY
from release import PublicationError

ARCHIVE_REF = 'refs/heads/student-work'
MAIN_REF = 'refs/heads/main'


def environment(authenticated=False):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_') or key in {'GIT_CONFIG_NOSYSTEM'}}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Course archive',
                       GIT_AUTHOR_EMAIL='course-archive@users.noreply.github.com',
                       GIT_COMMITTER_NAME='Course archive',
                       GIT_COMMITTER_EMAIL='course-archive@users.noreply.github.com')
    if authenticated:
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            raise PublicationError('archive authentication token is unavailable')
        credential = base64.b64encode(('x-access-token:' + token).encode()).decode()
        environment.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                           GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + credential)
    return environment


def git(repository, *arguments, input=None, authenticated=False):
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                             '-c', 'http.followRedirects=false',
                             '-C', str(repository), *arguments],
                            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=environment(authenticated), timeout=120, check=False)
    if result.returncode:
        # Avoid exposing remote URL credentials in error text.
        raise PublicationError('Archive Git operation failed: ' + arguments[0])
    return result.stdout


def remote_heads(repository):
    output = git(repository, 'ls-remote', '--refs', 'origin', MAIN_REF, ARCHIVE_REF)
    refs = {}
    for line in output.decode('ascii').splitlines():
        parts = line.split('\t')
        if len(parts) != 2 or not SHA.fullmatch(parts[0]) or parts[1] not in {MAIN_REF, ARCHIVE_REF}:
            raise PublicationError('unexpected remote ref response')
        if parts[1] in refs:
            raise PublicationError('ambiguous remote ref response')
        refs[parts[1]] = parts[0]
    return refs


def require_heads(repository, expected_main, expected_parent):
    if not isinstance(expected_main, str) or not SHA.fullmatch(expected_main):
        raise PublicationError('main code must be pinned to a full commit')
    if expected_parent is not None and (not isinstance(expected_parent, str) or not SHA.fullmatch(expected_parent)):
        raise PublicationError('archive parent must be a full commit, or None on a first run')
    refs = remote_heads(repository)
    if refs.get(MAIN_REF) != expected_main:
        raise PublicationError('main changed; archive candidate is superseded')
    if refs.get(ARCHIVE_REF) != expected_parent:
        raise PublicationError('archive head changed; re-read and rebuild candidate')


def create_commit(repository, snapshot, expected_parent, *, main_commit):
    """Construct only manifest-listed regular blobs and a commit with one parent, or a root on a first run."""
    archive = ArchiveSnapshot(snapshot)
    if not isinstance(main_commit, str) or not SHA.fullmatch(main_commit):
        raise PublicationError('accepted snapshot needs an exact main code identity')
    with tempfile.TemporaryDirectory(prefix='archive-index-') as temporary:
        index = Path(temporary) / 'index'
        # A private index prevents inherited checkout files entering the archive tree.
        entries = []
        files = ['publication.json']
        for slug, entry in archive.groups.items():
            if entry['snapshot']:
                for name in entry['snapshot']['files']:
                    files.append(f'{slug}/{name}')
        for name in sorted(files):
            if name == 'publication.json':
                content = archive.payload
            else:
                slug, relative = name.split('/', 1)
                content = archive.read_file(slug, relative)
            sha = git(repository, 'hash-object', '-w', '--stdin', input=content).decode().strip()
            entries.append(f'100644 {sha}\t{name}\0'.encode())
        def indexed(*arguments, input=None):
            result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(repository), *arguments],
                                    input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={key: value for key, value in os.environ.items() if not key.startswith('GIT_')} |
                                        {'GIT_INDEX_FILE': str(index), 'GIT_CONFIG_GLOBAL': os.devnull,
                                         'GIT_CONFIG_NOSYSTEM': '1'}, timeout=120, check=False)
            if result.returncode:
                raise PublicationError('cannot construct archive tree')
            return result.stdout
        indexed('read-tree', '--empty')
        indexed('update-index', '-z', '--index-info', input=b''.join(entries))
        tree = indexed('write-tree').decode().strip()
    arguments = ['commit-tree', tree]
    if expected_parent:
        if tree == git(repository, 'rev-parse', expected_parent + '^{tree}').decode().strip():
            return expected_parent
        arguments.extend(['-p', expected_parent])
    message = 'Accept validated student snapshots\n\nMain-Commit: ' + main_commit + '\n'
    return git(repository, *arguments, input=message.encode()).decode().strip()


def publish_commit(repository, commit, expected_main, expected_parent, *, authenticated=False):
    """Recheck identity and push normally: competing siblings cannot overwrite."""
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise PublicationError('invalid archive candidate commit')
    if commit != expected_parent:
        parents = git(repository, 'rev-list', '--parents', '-n', '1', commit).decode().strip().split()
        if parents != [commit] + ([expected_parent] if expected_parent else []):
            raise PublicationError('candidate does not have the expected archive parent')
    require_heads(repository, expected_main, expected_parent)
    if commit != expected_parent:
        # No force and no broad refs: the only writable destination is student-work.
        git(repository, 'push', 'origin', commit + ':' + ARCHIVE_REF, authenticated=authenticated)
    refs = remote_heads(repository)
    if refs.get(ARCHIVE_REF) != commit:
        raise PublicationError('archive push could not be confirmed')
    return commit


def init_remote(folder, repository):
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or repository.endswith('.git'):
        raise PublicationError('archive remote must be the trusted course repository')
    folder = Path(folder)
    folder.mkdir()
    git(folder, 'init', '--bare', '--quiet')
    git(folder, 'remote', 'add', 'origin', 'https://github.com/' + repository + '.git')
    return folder


def bounded_process(repository, arguments, measured, *, stdout=None, limit=MAX_TRANSFER_BYTES, seconds=180):
    """Bound shallow transfer and tar output while the child is still running."""
    command = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'http.followRedirects=false',
               '-C', str(repository), *arguments]
    measured = Path(measured)
    started = time.monotonic()
    def size():
        return (sum(path.stat().st_size for path in measured.rglob('*') if path.is_file())
                if measured.is_dir() else measured.stat().st_size if measured.exists() else 0)
    with subprocess.Popen(command, stdout=stdout or subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=environment()) as process:
        try:
            while True:
                if size() > limit or time.monotonic() - started > seconds:
                    raise PublicationError('archive transfer exceeded byte/time bounds')
                try:
                    status = process.wait(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if status or size() > limit:
                raise PublicationError('archive transfer failed or exceeded byte bounds')
        except BaseException:
            process.kill()
            process.wait()
            raise
    return size()


def fetch_commit(repository, commit):
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise PublicationError('archive transfer must use a full commit')
    transferred = bounded_process(repository, ['fetch', '--depth=1', '--no-tags', 'origin', commit],
                                  Path(repository) / 'objects')
    if git(repository, 'rev-parse', 'FETCH_HEAD').decode().strip() != commit:
        raise PublicationError('fetched archive commit differs from pin')
    return transferred


def snapshot_from_git(repository, commit, destination):
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise PublicationError('snapshot requires an exact archive commit')
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='.archive-transfer-', dir=destination.parent) as temporary:
        listing = Path(temporary) / 'inventory'
        with listing.open('xb') as output:
            bounded_process(repository, ['ls-tree', '-r', '-z', '-l', '--full-tree', commit],
                            listing, stdout=output, limit=MAX_MANIFEST_BYTES)
        records = {}
        for row in listing.read_bytes().split(b'\0'):
            if not row:
                continue
            try:
                header, name = row.split(b'\t', 1)
                mode, kind, blob, size = header.decode('ascii').split()
                name = name.decode('utf-8')
                if mode != '100644' or kind != 'blob' or not SHA.fullmatch(blob) or not size.isdigit() or name in records:
                    raise ValueError('unexpected Git object')
                records[name] = {'size': int(size), 'blob_sha': blob}
            except (ValueError, UnicodeError) as error:
                raise PublicationError('invalid archive Git inventory') from error
        manifest = records.get('publication.json')
        if not manifest or manifest['size'] > MAX_MANIFEST_BYTES:
            raise PublicationError('missing/oversized archive Git manifest')
        payload = git(repository, 'cat-file', 'blob', commit + ':publication.json')
        groups = decode_archive(payload)
        expected = {'publication.json': manifest}
        expected.update({f'{slug}/{name}': {'size': record['size'], 'blob_sha': record['blob_sha']}
                         for slug, entry in groups.items() if entry['snapshot']
                         for name, record in entry['snapshot']['files'].items()})
        if records != expected:
            raise PublicationError('archive Git inventory differs before expansion')
        tar = Path(temporary) / 'snapshot.tar'
        with tar.open('xb') as output:
            bounded_process(repository, ['archive', '--format=tar', commit], tar, stdout=output)
        import hashlib
        checksum = hashlib.sha256()
        with tar.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                checksum.update(chunk)
        unpack_snapshot(tar, destination, checksum.hexdigest(), git_directories=True)
    return ArchiveSnapshot(destination, commit)


def parent_snapshot(repository, commit, destination):
    """The accepted parent in this writer repository, or the empty archive of a first run."""
    if commit is None:
        return empty_archive(destination)
    fetch_commit(repository, commit)
    return snapshot_from_git(repository, commit, destination)


def read_remote_snapshot(repository, commit, destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='.archive-fetch-', dir=destination.parent) as temporary:
        checkout = init_remote(Path(temporary) / 'repository.git', repository)
        transferred = fetch_commit(checkout, commit)
        snapshot = snapshot_from_git(checkout, commit, destination)
    return snapshot, {'archive_transfer_bytes': transferred}
