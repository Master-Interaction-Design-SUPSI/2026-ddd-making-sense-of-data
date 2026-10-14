"""Prepare an annotated archive freeze and reproduce its exact code/archive pair.

These local operations do not establish live verification or publish a tag.
The maintainer must separately verify the release and authorize the protected
tag push. Included bytes are durable; excluded files and external services are
outside the archive guarantee.
"""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import archive_git
import archive_release
from archive_state import ArchiveSnapshot
from publication_state import RecoveryError, unique_keys
from publish_site import encode, git_revision, validate_release, worktree_clean

TAG = 'final-2026'
MAX_RECORD_BYTES = 16384


def validate_record(record):
    if (not isinstance(record, dict) or set(record) != {'version', 'tag', 'release'}
            or type(record['version']) is not int or record['version'] != 1 or record['tag'] != TAG):
        raise RecoveryError('invalid freeze record')
    release = validate_release(record['release'])
    if release['version'] != 2:
        raise RecoveryError('freeze requires an exact V2 release pair')
    return record


def require_frozen(archive):
    if any(entry['snapshot'] and entry['state'] not in {'hidden', 'frozen'} for entry in archive.groups.values()):
        raise RecoveryError('freeze requires all visible groups to be frozen')
    if not archive.served():
        raise RecoveryError('freeze requires at least one visible accepted snapshot')


def prepare_record(release, archive, main_commit, main_tree):
    require_frozen(archive)
    archive_release.reconstruct(release, archive, main_commit, main_tree)
    return validate_record({'version': 1, 'tag': TAG, 'release': release})


def reproduce(record, archive, main_commit, main_tree):
    validate_record(record)
    require_frozen(archive)
    return archive_release.reconstruct(record['release'], archive, main_commit, main_tree)


def create_local_tag(repository, record, main_commit, main_tree):
    """Create once in an isolated local Git repository; never push or move refs."""
    validate_record(record)
    release = record['release']
    with tempfile.TemporaryDirectory(prefix='ddd-freeze-check-') as temporary:
        archive = archive_git.snapshot_from_git(repository, release['archive_commit'], Path(temporary) / 'snapshot')
        reproduce(record, archive, main_commit, main_tree)
    # mktag creates the annotation without allowing Git to choose another target.
    tagger = archive_git.git(repository, 'var', 'GIT_COMMITTER_IDENT').decode().strip()
    header = (f"object {release['archive_commit']}\ntype commit\ntag {TAG}\ntagger {tagger}\n\n").encode()
    tag_object = archive_git.git(repository, 'mktag', input=header + encode(record)).decode().strip()
    archive_git.git(repository, 'update-ref', 'refs/tags/' + TAG, tag_object, '0' * 40)
    return tag_object


def verify_local_tag(repository, main_commit, main_tree):
    ref = 'refs/tags/' + TAG
    if archive_git.git(repository, 'cat-file', '-t', ref).decode().strip() != 'tag':
        raise RecoveryError('freeze tag must be annotated')
    size = archive_git.git(repository, 'cat-file', '-s', ref).decode().strip()
    if not size.isdigit() or int(size) > MAX_RECORD_BYTES:
        raise RecoveryError('freeze annotation exceeds its limit')
    payload = archive_git.git(repository, 'cat-file', '-p', ref)
    if len(payload) > MAX_RECORD_BYTES:
        raise RecoveryError('freeze annotation exceeds its limit')
    try:
        header, body = payload.split(b'\n\n', 1)
        record = validate_record(json.loads(body, object_pairs_hook=unique_keys))
    except (ValueError, TypeError) as error:
        raise RecoveryError('invalid freeze annotation') from error
    lines = header.decode().splitlines()
    if lines[:3] != [f"object {record['release']['archive_commit']}", 'type commit', 'tag ' + TAG]:
        raise RecoveryError('freeze annotation target differs from release')
    if archive_git.git(repository, 'rev-parse', ref + '^{commit}').decode().strip() != record['release']['archive_commit']:
        raise RecoveryError('freeze tag points to a different archive')
    with tempfile.TemporaryDirectory(prefix='ddd-freeze-verify-') as temporary:
        archive = archive_git.snapshot_from_git(repository, record['release']['archive_commit'],
                                                Path(temporary) / 'snapshot')
        reproduce(record, archive, main_commit, main_tree)
    return record


def read_record(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
        raise RecoveryError('missing or oversized freeze record')
    return validate_record(json.loads(path.read_bytes(), object_pairs_hook=unique_keys))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'reproduce'):
        command = commands.add_parser(name)
        command.add_argument('--record' if name == 'reproduce' else '--release', required=True)
        command.add_argument('--repository', required=True)
        command.add_argument('--archive-commit', required=True)
        command.add_argument('--main-commit', required=True)
        command.add_argument('--main-tree', required=True)
        if name == 'prepare':
            command.add_argument('--out', required=True)
    for name in ('create-local-tag', 'verify-local-tag'):
        command = commands.add_parser(name)
        command.add_argument('--repository', required=True)
        command.add_argument('--main-commit', required=True)
        command.add_argument('--main-tree', required=True)
        if name == 'create-local-tag':
            command.add_argument('--record', required=True)
    args = parser.parse_args()
    if (git_revision('HEAD') != args.main_commit or git_revision('HEAD^{tree}') != args.main_tree
            or not worktree_clean()):
        raise RecoveryError('freeze requires a clean checkout of the exact recorded code pair')
    if args.command == 'create-local-tag':
        print(create_local_tag(args.repository, read_record(args.record), args.main_commit, args.main_tree))
    elif args.command == 'verify-local-tag':
        print(encode(verify_local_tag(args.repository, args.main_commit, args.main_tree)).decode())
    else:
        with tempfile.TemporaryDirectory(prefix='ddd-freeze-cli-') as temporary:
            archive = archive_git.snapshot_from_git(args.repository, args.archive_commit, Path(temporary) / 'snapshot')
            if args.command == 'prepare':
                path = Path(args.release)
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                    raise RecoveryError('missing or oversized release record')
                release = json.loads(path.read_bytes(), object_pairs_hook=unique_keys)
                record = prepare_record(release, archive, args.main_commit, args.main_tree)
                with Path(args.out).open('xb') as stream:
                    stream.write(encode(record))
            else:
                print(encode(reproduce(read_record(args.record), archive, args.main_commit, args.main_tree)).decode())


if __name__ == '__main__':
    main()
