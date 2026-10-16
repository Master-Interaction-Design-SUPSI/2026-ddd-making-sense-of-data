"""Create and verify local V1 recovery bundles without publishing or changing refs.

The bundle format is intentionally self-contained: ``site/`` is the original
complete V1 Pages artifact, and ``capture.json`` binds every file to the V1
release and its source code commit/tree.  A capture is create-once; callers
should retain it as immutable evidence.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from archive_state import BoundedTarInfo
from content_contract import MAX_SITE_BYTES
from publication_state import (MANIFEST, MAX_MANIFEST_BYTES, RecoveryError,
                               decode_manifest, unique_keys)
from publish_site import RELEASE, encode, validate_release


CAPTURE = 'capture.json'
CAPTURE_VERSION = 1
ARCHIVE_SHA = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_INVENTORY_ENTRIES = 10000
MAX_RELEASE_BYTES = 4096
MAX_CAPTURE_METADATA_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_TAR_BYTES = MAX_SITE_BYTES + 16 * 1024 * 1024
MAX_ARTIFACT_PATH_BYTES = 576
MAX_ARTIFACT_PATH_DEPTH = 32


def _sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def _site_digest_from_inventory(inventory):
    records = []
    for name in sorted(inventory, key=PurePosixPath):
        if name != RELEASE:
            record = inventory[name]
            records.append([name, record['size'], record['sha256']])
    return _sha256(encode(records))


def _safe_relative(name):
    if (not isinstance(name, str) or not name or len(name) > 1024 or '\\' in name
            or name.startswith('/') or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise RecoveryError('capture contains an unsafe path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in name.split('/')):
        raise RecoveryError('capture contains an unsafe path')
    return path


def _inventory(folder):
    """Bound and inventory regular files/directories, rejecting links/special files."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise RecoveryError('site artifact must be a regular directory')
    files, directories, pending = {}, set(), []
    total = 0
    for path in folder.rglob('*'):
        relative = path.relative_to(folder).as_posix()
        _safe_relative(relative)
        if path.is_symlink():
            raise RecoveryError('site artifact contains a symbolic link')
        if path.is_dir():
            directories.add(relative)
            if len(pending) + len(directories) > MAX_INVENTORY_ENTRIES:
                raise RecoveryError('site artifact exceeds the inventory entry limit')
            continue
        if not path.is_file():
            raise RecoveryError('site artifact contains a special file')
        size = path.stat().st_size
        total += size
        pending.append((relative, path, size))
        if len(pending) + len(directories) > MAX_INVENTORY_ENTRIES:
            raise RecoveryError('site artifact exceeds the inventory entry limit')
        if total > MAX_SITE_BYTES:
            raise RecoveryError('site artifact exceeds the recovery byte limit')
    if len(pending) + len(directories) > MAX_INVENTORY_ENTRIES:
        raise RecoveryError('site artifact exceeds the inventory entry limit')
    for relative, _path, expected_size in pending:
        if relative == RELEASE and expected_size > MAX_RELEASE_BYTES:
            raise RecoveryError('V1 release metadata exceeds its byte limit')
        if relative == MANIFEST and expected_size > MAX_MANIFEST_BYTES:
            raise RecoveryError('V1 manifest exceeds its byte limit')
    hashed_total = 0
    for relative, path, expected_size in pending:
        content_hash = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                content_hash.update(chunk)
                if size > expected_size or size > MAX_SITE_BYTES:
                    raise RecoveryError('site artifact changed or exceeds its byte limit')
        if size != expected_size:
            raise RecoveryError('site artifact changed during inventory')
        hashed_total += size
        if hashed_total > MAX_SITE_BYTES:
            raise RecoveryError('site artifact exceeds the recovery byte limit')
        files[relative] = {'size': size, 'sha256': content_hash.hexdigest()}
    if len(files) + len(directories) > MAX_INVENTORY_ENTRIES:
        raise RecoveryError('site artifact exceeds the inventory entry limit')
    return dict(sorted(files.items())), sorted(directories)


def _validate_v1_site(site, release):
    release = validate_release(release)
    if release['version'] != 1:
        raise RecoveryError('V1 capture cannot contain a V2 release')
    inventory, directories = _inventory(site)
    release_path = Path(site) / RELEASE
    manifest_path = Path(site) / MANIFEST
    if RELEASE not in inventory or MANIFEST not in inventory:
        raise RecoveryError('V1 artifact is missing release or publication manifest')
    if release_path.stat().st_size > MAX_RELEASE_BYTES:
        raise RecoveryError('V1 release metadata exceeds its byte limit')
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise RecoveryError('V1 manifest exceeds its byte limit')
    try:
        embedded_release = json.loads(release_path.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, OSError) as error:
        raise RecoveryError('V1 artifact has an invalid release record') from error
    if embedded_release != release:
        raise RecoveryError('V1 artifact release differs from the supplied verified release')
    manifest = manifest_path.read_bytes()
    if _sha256(manifest) != release['manifest_sha256']:
        raise RecoveryError('V1 artifact manifest differs from the release digest')
    groups = decode_manifest(manifest)
    for slug, group in groups.items():
        for name, record in group['files'].items():
            relative = f'{slug}/{name}'
            actual = inventory.get(relative)
            if actual is None or actual != record:
                raise RecoveryError(f'V1 artifact content differs from manifest: {relative}')
    if _site_digest_from_inventory(inventory) != release['site_sha256']:
        raise RecoveryError('V1 artifact site digest differs from the release')
    return inventory, directories, manifest, groups


def _metadata(capture):
    path = Path(capture)
    if path.is_symlink() or not path.is_dir():
        raise RecoveryError('capture must be a regular directory')
    expected_root = {CAPTURE, 'site'}
    actual_root = {child.name for child in path.iterdir()}
    if actual_root != expected_root:
        raise RecoveryError('capture contains missing or extra root entries')
    capture_file = path / CAPTURE
    if (capture_file.is_symlink() or not capture_file.is_file()
            or capture_file.stat().st_size > MAX_CAPTURE_METADATA_BYTES):
        raise RecoveryError('capture metadata is missing or unsafe')
    try:
        value = json.loads(capture_file.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, OSError) as error:
        raise RecoveryError('capture metadata is invalid') from error
    required = {'version', 'release', 'source_commit', 'source_tree', 'site_sha256',
                'manifest_sha256', 'inventory', 'directories'}
    if not isinstance(value, dict) or set(value) != required or type(value['version']) is not int or value['version'] != CAPTURE_VERSION:
        raise RecoveryError('unsupported capture metadata schema')
    if not isinstance(value['inventory'], dict) or not isinstance(value['directories'], list):
        raise RecoveryError('capture inventory schema is invalid')
    if len(value['inventory']) + len(value['directories']) > MAX_INVENTORY_ENTRIES:
        raise RecoveryError('capture inventory exceeds its entry limit')
    release = validate_release(value['release'])
    if release['version'] != 1:
        raise RecoveryError('capture contains a V2 release')
    if (value['source_commit'] != release['source_commit'] or value['source_tree'] != release['source_tree']
            or value['site_sha256'] != release['site_sha256']
            or value['manifest_sha256'] != release['manifest_sha256']):
        raise RecoveryError('capture code/release identity does not match')
    if not value['inventory']:
        raise RecoveryError('capture inventory is empty or invalid')
    for name, record in value['inventory'].items():
        _safe_relative(name)
        if (not isinstance(record, dict) or set(record) != {'size', 'sha256'}
                or type(record['size']) is not int or record['size'] < 0
                or not isinstance(record['sha256'], str) or not SHA256.fullmatch(record['sha256'])):
            raise RecoveryError('capture inventory record is invalid')
    if (any(not isinstance(name, str) for name in value['directories'])
            or value['directories'] != sorted(set(value['directories']))):
        raise RecoveryError('capture directory inventory is invalid')
    for name in value['directories']:
        _safe_relative(name)
    if sum(record['size'] for record in value['inventory'].values()) > MAX_SITE_BYTES:
        raise RecoveryError('capture inventory exceeds its byte limit')
    return path, value


def verify_capture(capture, expected_release=None):
    """Revalidate the complete V1 site and return its trusted capture metadata."""
    path, value = _metadata(capture)
    if expected_release is not None:
        expected_release = validate_release(expected_release)
        if expected_release['version'] != 1 or expected_release != value['release']:
            raise RecoveryError('capture does not match the expected V1 release')
    inventory, directories, manifest, _groups = _validate_v1_site(path / 'site', value['release'])
    if inventory != value['inventory'] or directories != value['directories']:
        raise RecoveryError('capture inventory differs from its site bytes')
    if _sha256(manifest) != value['manifest_sha256']:
        raise RecoveryError('capture manifest digest differs')
    return value


def capture_v1(site_folder, release, destination):
    """Capture the supplied original V1 artifact after matching it to its release.

    This proves artifact/release consistency only. The caller must separately
    establish that ``release`` is the currently verified live deployment.
    """
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('capture destination must not already exist')
    inventory, directories, manifest, _groups = _validate_v1_site(site_folder, release)
    metadata = {
        'version': CAPTURE_VERSION,
        'release': release,
        'source_commit': release['source_commit'],
        'source_tree': release['source_tree'],
        'site_sha256': release['site_sha256'],
        'manifest_sha256': _sha256(manifest),
        'inventory': inventory,
        'directories': directories,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.v1-capture-', dir=destination.parent))
    try:
        shutil.copytree(site_folder, temporary / 'site', symlinks=True)
        (temporary / CAPTURE).write_bytes(encode(metadata))
        verify_capture(temporary)
        if destination.exists() or destination.is_symlink():
            raise RecoveryError('capture destination appeared during creation')
        os.rename(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return verify_capture(destination)


def _artifact_path(member):
    """Normalize a Pages tar member without using its mode or path for extraction."""
    name = member.name
    if (not isinstance(name, str) or not name or len(name.encode('utf-8')) > MAX_ARTIFACT_PATH_BYTES
            or '\\' in name or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise RecoveryError('Pages artifact contains an unsafe path')
    if name in {'.', './'} and member.type == tarfile.DIRTYPE:
        return '.'
    if name.startswith('./'):
        name = name[2:]
    if name.startswith('/'):
        raise RecoveryError('Pages artifact contains an unsafe path')
    if member.type == tarfile.DIRTYPE and name.endswith('/'):
        name = name[:-1]
    parts = name.split('/')
    if (not name or len(parts) > MAX_ARTIFACT_PATH_DEPTH
            or any(part in {'', '.', '..'} for part in parts)):
        raise RecoveryError('Pages artifact contains an unsafe path')
    return '/'.join(parts)


def _copy_artifact_member(source, member, destination):
    stream = source.extractfile(member)
    if stream is None:
        raise RecoveryError('Pages artifact file has no readable payload')
    remaining = member.size
    with stream, destination.open('xb') as output:
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise RecoveryError('Pages artifact payload is shorter than its advertised size')
            output.write(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise RecoveryError('Pages artifact payload exceeds its advertised size')


def capture_v1_artifact(artifact_tar, expected_v1_release, destination):
    """Safely capture a verified V1 Pages site from a plain tar artifact.

    Tar-provided paths, modes, owners and timestamps are never used to extract
    files.  The original V1 validator remains the authority for release, file,
    manifest and complete-site digests after bounded extraction.
    """
    artifact_tar = Path(artifact_tar)
    destination = Path(destination)
    release = validate_release(expected_v1_release)
    if release['version'] != 1:
        raise RecoveryError('V1 artifact capture requires an expected V1 release')
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('capture destination must not already exist')
    if (artifact_tar.is_symlink() or not artifact_tar.is_file()
            or artifact_tar.stat().st_size > MAX_ARTIFACT_TAR_BYTES):
        raise RecoveryError('Pages artifact tar is missing, unsafe or oversized')

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.v1-artifact-', dir=destination.parent))
    site = temporary / 'site'
    site.mkdir()
    try:
        files, directories, names = {}, set(), set()
        advertised_total = 0
        try:
            with artifact_tar.open('rb') as raw, tarfile.open(
                    fileobj=raw, mode='r:', tarinfo=BoundedTarInfo) as archive:
                members = []
                for member in archive:
                    if getattr(archive, '_archive_headers', 0) > MAX_INVENTORY_ENTRIES:
                        raise RecoveryError('Pages artifact exceeds the inventory header limit')
                    if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}:
                        raise RecoveryError('Pages artifact contains a link or special file')
                    name = _artifact_path(member)
                    if name in names:
                        raise RecoveryError('Pages artifact contains duplicate paths')
                    names.add(name)
                    if name == '.':
                        if member.type != tarfile.DIRTYPE:
                            raise RecoveryError('Pages artifact root entry must be a directory')
                        members.append((member, name))
                        continue
                    if member.type == tarfile.DIRTYPE:
                        directories.add(name)
                    else:
                        if type(member.size) is not int or member.size < 0:
                            raise RecoveryError('Pages artifact has an invalid advertised file size')
                        advertised_total += member.size
                        if advertised_total > MAX_SITE_BYTES:
                            raise RecoveryError('Pages artifact exceeds the expanded site byte limit')
                        if name == RELEASE and member.size > MAX_RELEASE_BYTES:
                            raise RecoveryError('V1 release metadata exceeds its byte limit')
                        if name == MANIFEST and member.size > MAX_MANIFEST_BYTES:
                            raise RecoveryError('V1 manifest exceeds its byte limit')
                        files[name] = member.size
                    members.append((member, name))
                raw_headers = getattr(archive, '_archive_headers', 0)
                if raw_headers > MAX_INVENTORY_ENTRIES:
                    raise RecoveryError('Pages artifact exceeds the inventory header limit')
                if RELEASE not in files or MANIFEST not in files:
                    raise RecoveryError('V1 artifact is missing release or publication manifest')
                declared_paths = set(files) | directories
                materialized_paths = set(declared_paths)
                for name in declared_paths:
                    if any('/'.join(name.split('/')[:index]) in files
                           for index in range(1, len(name.split('/')))):
                        raise RecoveryError('Pages artifact contains a file/parent collision')
                    parts = name.split('/')
                    materialized_paths.update('/'.join(parts[:index]) for index in range(1, len(parts)))
                if raw_headers + len(materialized_paths - declared_paths) > MAX_INVENTORY_ENTRIES:
                    raise RecoveryError('Pages artifact exceeds the inventory entry limit')

                # Only after the full archive inventory and metadata caps are
                # known do we read any member payload.
                for directory in sorted(directories, key=lambda value: (value.count('/'), value)):
                    (site / directory).mkdir(parents=True, exist_ok=True)
                copied_total = 0
                for member, name in members:
                    if name == '.' or member.type == tarfile.DIRTYPE:
                        continue
                    path = site / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _copy_artifact_member(archive, member, path)
                    copied_total += path.stat().st_size
                    if path.stat().st_size != files[name] or copied_total > MAX_SITE_BYTES:
                        raise RecoveryError('Pages artifact payload differs from its advertised size')
                if copied_total != advertised_total:
                    raise RecoveryError('Pages artifact expanded size differs from its advertised inventory')
        except RecoveryError:
            raise
        except (tarfile.TarError, EOFError, OSError, ValueError, OverflowError) as error:
            raise RecoveryError('Pages artifact tar is invalid or truncated') from error
        result = capture_v1(site, release, destination)
        return result
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _capture_fingerprint(metadata):
    return _sha256(encode(metadata))


def stage_rollback(capture, destination, current_archive_commit, expected_release=None):
    """Stage original V1 bytes locally and record the V2 archive head to preserve.

    This function has no remote or Git mutation path. The caller owns the
    separate deployment review and must compare the archive ref before/after.
    """
    if not isinstance(current_archive_commit, str) or not ARCHIVE_SHA.fullmatch(current_archive_commit):
        raise RecoveryError('rollback plan needs the exact current V2 archive commit')
    metadata = verify_capture(capture, expected_release=expected_release)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('rollback staging destination must not already exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.v1-rollback-', dir=destination.parent))
    try:
        shutil.copytree(Path(capture) / 'site', temporary / 'site', symlinks=True)
        staged_inventory, staged_directories, _manifest, _groups = _validate_v1_site(temporary / 'site', metadata['release'])
        if staged_inventory != metadata['inventory'] or staged_directories != metadata['directories']:
            raise RecoveryError('staged V1 site differs from the verified capture')
        plan = {
            'version': 1,
            'operation': 'stage-v1-rollback',
            'deployment_performed': False,
            'archive_head_to_preserve': current_archive_commit,
            'capture_sha256': _capture_fingerprint(metadata),
            'release': metadata['release'],
            'source_commit': metadata['source_commit'],
            'source_tree': metadata['source_tree'],
            'site_sha256': metadata['site_sha256'],
            'manifest_sha256': metadata['manifest_sha256'],
            'inventory': metadata['inventory'],
            'directories': metadata['directories'],
        }
        (temporary / 'rollback-plan.json').write_bytes(encode(plan))
        if destination.exists() or destination.is_symlink():
            raise RecoveryError('rollback staging destination appeared during creation')
        os.rename(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return plan


def compare_v1_seed(capture, archive_snapshot):
    """Compare visible seeded inventory and content bytes, allowing schema metadata changes.

    Returns an auditable result; V2 archive schema/state/blob metadata is reported
    as intentional and is not compared as whole-site output.
    """
    metadata = verify_capture(capture)
    _path, _directories, _manifest, v1_groups = _validate_v1_site(Path(capture) / 'site', metadata['release'])
    # V1 omitted this field when it had the default; archive v2 makes it explicit.
    v1_groups = {slug: (group | {'require_visual': group.get('require_visual', True)})
                 for slug, group in v1_groups.items()}
    served = archive_snapshot.served()
    differences = []
    compared = 0
    intentional = [
        'Archive manifest uses schema v2 while the captured Pages manifest uses v1.',
        'Archive records add state, contract version, tree, exclusions and Git blob SHA metadata.',
        'The comparison covers published group inventory and file bytes, not whole-site renderer output.',
    ]
    if served != v1_groups:
        expected_slugs, actual_slugs = set(v1_groups), set(served)
        for slug in sorted(expected_slugs - actual_slugs):
            differences.append(f'{slug}: missing from visible archive inventory')
        for slug in sorted(actual_slugs - expected_slugs):
            differences.append(f'{slug}: unexpected in visible archive inventory')
        for slug in sorted(expected_slugs & actual_slugs):
            if served[slug] != v1_groups[slug]:
                differences.append(f'{slug}: visible inventory metadata differs')
    for slug in sorted(set(v1_groups) & set(served)):
        expected_files = v1_groups[slug]['files']
        actual_files = served[slug]['files']
        for name in sorted(set(expected_files) | set(actual_files)):
            relative = f'{slug}/{name}'
            if name not in actual_files:
                continue
            try:
                actual = archive_snapshot.read_file(slug, name)
            except Exception as error:
                differences.append(f'{relative}: archive bytes could not be verified ({error})')
                continue
            if name not in expected_files:
                differences.append(f'{relative}: unexpected published archive file')
            else:
                expected_path = Path(capture) / 'site' / slug / name
                expected = expected_path.read_bytes()
                compared += 1
                if actual != expected:
                    differences.append(f'{relative}: published bytes differ')
    return {'matches': not differences, 'differences': differences,
            'intentional_metadata_differences': intentional,
            'captured_manifest_sha256': metadata['manifest_sha256'],
            'archive_manifest_sha256': _sha256(archive_snapshot.payload),
            'published_files_compared': compared}


def _read_json(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RecoveryError('JSON input is missing, unsafe or oversized')
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, OSError) as error:
        raise RecoveryError('JSON input is invalid') from error


def main(argv=None):
    """Local-only recovery CLI. No command writes to GitHub or deploys Pages."""
    parser = argparse.ArgumentParser(description=(
        'Capture and stage V1 recovery locally. Capture validates artifact/release consistency; '
        'the caller must separately establish that the release is verified and live.'
    ))
    commands = parser.add_subparsers(dest='command', required=True)

    capture = commands.add_parser('capture-v1', help='capture a supplied original V1 site artifact')
    capture.add_argument('--site', required=True, help='original complete V1 site artifact directory')
    capture.add_argument('--release', required=True, help='V1 release.json file')
    capture.add_argument('--out', required=True, help='new capture directory')

    artifact = commands.add_parser('capture-v1-artifact', help='capture a V1 Pages site from its original tar artifact')
    artifact.add_argument('--artifact', required=True, help='original complete Pages tar artifact')
    artifact.add_argument('--release', required=True, help='expected verified V1 release.json file')
    artifact.add_argument('--out', required=True, help='new capture directory')

    verify = commands.add_parser('verify-capture', help='verify all capture metadata and site bytes')
    verify.add_argument('--capture', required=True)
    verify.add_argument('--expected-release', help='optional expected V1 release.json file')

    rollback = commands.add_parser('stage-rollback', help='stage V1 bytes locally; does not deploy')
    rollback.add_argument('--capture', required=True)
    rollback.add_argument('--archive-commit', required=True, help='exact student-work commit to preserve')
    rollback.add_argument('--out', required=True, help='new local staging directory')
    rollback.add_argument('--expected-release', help='optional expected V1 release.json file')

    compare = commands.add_parser('compare-seed', help='compare V1 content with a local archive snapshot')
    compare.add_argument('--capture', required=True)
    compare.add_argument('--archive-snapshot', required=True, help='materialized archive snapshot directory')
    compare.add_argument('--archive-commit', required=True, help='full archive commit pin')

    args = parser.parse_args(argv)
    try:
        if args.command == 'capture-v1':
            release = _read_json(args.release, MAX_RELEASE_BYTES)
            result = capture_v1(args.site, release, args.out)
        elif args.command == 'capture-v1-artifact':
            release = _read_json(args.release, MAX_RELEASE_BYTES)
            result = capture_v1_artifact(args.artifact, release, args.out)
        elif args.command == 'verify-capture':
            expected = _read_json(args.expected_release, MAX_RELEASE_BYTES) if args.expected_release else None
            result = verify_capture(args.capture, expected_release=expected)
        elif args.command == 'stage-rollback':
            expected = _read_json(args.expected_release, MAX_RELEASE_BYTES) if args.expected_release else None
            result = stage_rollback(args.capture, args.out, args.archive_commit, expected_release=expected)
        else:
            from archive_state import ArchiveSnapshot
            archive = ArchiveSnapshot(args.archive_snapshot, args.archive_commit)
            result = compare_v1_seed(args.capture, archive)
    except (OSError, ValueError, RecoveryError) as error:
        sys.stderr.write(str(error) + '\n')
        return 2
    sys.stdout.write(encode(result).decode('utf-8'))
    if args.command == 'compare-seed' and not result['matches']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

