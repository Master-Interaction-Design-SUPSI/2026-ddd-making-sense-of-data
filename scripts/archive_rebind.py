"""Prepare and independently admit an explicit group identity rebind.

This module is local-only. It builds a candidate and a migration specification;
it does not access GitHub or update an archive ref.
"""

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from archive_state import MANIFEST, ArchiveSnapshot, canonical, encode_archive
from archive_transaction import projected_site
from group_registry import RESERVED_SLUGS, SLUG, Group, load_registry
from publication_state import RecoveryError, unique_keys


SPEC_VERSION = 1
MAX_SPEC_BYTES = 16 * 1024


def _registry_by_slug(registry):
    groups = {}
    for group in registry:
        if not isinstance(group, Group):
            raise RecoveryError('rebind registry must contain validated group records')
        if group.slug in groups:
            raise RecoveryError('rebind registry contains a duplicate slug')
        groups[group.slug] = group
    return groups


def _retired_slug(slug, repository, branch):
    identity = f'{repository.casefold()}\0{branch}'.encode('utf-8')
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    value = f'retired-{slug[:37]}-{suffix}'
    if len(value) > 63 or not SLUG.fullmatch(value) or value in RESERVED_SLUGS:
        raise RecoveryError('cannot derive a valid retired identity slug')
    return value


def _migration(previous, registry, slug, expected_repository, expected_branch):
    if not isinstance(previous, ArchiveSnapshot):
        raise RecoveryError('rebind requires a validated previous archive snapshot')
    if previous.commit is None:
        raise RecoveryError('rebind requires an exact archive parent commit')
    if not isinstance(slug, str) or len(slug) > 63 or not SLUG.fullmatch(slug):
        raise RecoveryError('rebind slug must be a valid group slug')
    configured = _registry_by_slug(registry)
    target = configured.get(slug)
    if target is None:
        raise RecoveryError('rebind slug is absent from the trusted registry')
    old = previous.groups.get(slug)
    if old is None:
        raise RecoveryError('rebind slug is absent from the archive parent')
    if old['snapshot'] is None:
        raise RecoveryError('rebind requires an accepted snapshot to preserve')
    if not isinstance(expected_repository, str) or not isinstance(expected_branch, str):
        raise RecoveryError('expected old repository and branch are required')
    if old['repository'] != expected_repository or old['branch'] != expected_branch:
        raise RecoveryError('expected old identity differs from the archive parent')
    if (target.repository.casefold(), target.branch) == (expected_repository.casefold(), expected_branch):
        raise RecoveryError('target registry identity does not differ from the old identity')

    retired_slug = _retired_slug(slug, expected_repository, expected_branch)
    if retired_slug in previous.groups or retired_slug in configured:
        raise RecoveryError('deterministic retired slug is already in use')
    spec = {
        'version': SPEC_VERSION,
        'expected_parent': previous.commit,
        'slug': slug,
        'retired_slug': retired_slug,
        'old_repository': expected_repository,
        'old_branch': expected_branch,
        'new_repository': target.repository,
        'new_branch': target.branch,
    }
    return configured, target, old, spec


def _expected_groups(previous, configured, target, old, spec):
    groups = copy.deepcopy(previous.groups)

    # Preserve removed identities and snapshots, while reflecting their hidden
    # state. Unrelated configured identities/settings must already agree; only
    # their trusted publication state may be reconciled in this operation.
    for slug, entry in groups.items():
        if slug not in configured:
            entry['state'] = 'hidden'
    for slug, group in configured.items():
        if slug == spec['slug']:
            continue
        entry = groups.get(slug)
        if entry is None:
            raise RecoveryError(f'{slug}: unrelated registry addition needs separate reconciliation')
        if (entry['repository'], entry['branch'], entry['require_visual'], entry['exclude']) != (
                group.repository, group.branch, group.require_visual, list(group.exclude)):
            raise RecoveryError(f'{slug}: unrelated registry identity/settings need separate reconciliation')
        entry['state'] = group.publication_state

    # The active slug now represents the new repository, with no invented
    # source revision. The retired row keeps the previous identity and snapshot.
    groups[spec['slug']] = {
        'repository': target.repository,
        'branch': target.branch,
        'state': target.publication_state,
        'require_visual': target.require_visual,
        'exclude': list(target.exclude),
        'snapshot': None,
    }
    retired = copy.deepcopy(old)
    retired['state'] = 'hidden'
    groups[spec['retired_slug']] = retired
    encode_archive(groups)
    return groups


def prepare_rebind(previous, registry, slug, expected_repository, expected_branch, destination):
    """Build a rebind candidate and return ``(snapshot, migration_spec)``.

    The old snapshot is moved intact to a deterministic retired slug. The new
    active row has no snapshot until the new repository is separately imported.
    """
    configured, target, old, spec = _migration(
        previous, registry, slug, expected_repository, expected_branch)
    groups = _expected_groups(previous, configured, target, old, spec)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RecoveryError('rebind destination must be new')
    with tempfile.TemporaryDirectory(prefix='.archive-rebind-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'snapshot'
        staged.mkdir()
        for target_slug, entry in groups.items():
            if entry['snapshot'] is None:
                continue
            source_slug = slug if target_slug == spec['retired_slug'] else target_slug
            folder = staged / target_slug
            folder.mkdir(parents=True)
            for name in entry['snapshot']['files']:
                target_file = folder / name
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(previous.read_file(source_slug, name))
        (staged / MANIFEST).write_bytes(encode_archive(groups))
        candidate = ArchiveSnapshot(staged, previous.commit)
        projected_site(candidate.folder)
        staged.rename(destination)
    return ArchiveSnapshot(destination, previous.commit), spec


def admit_rebind(candidate, registry, previous, migration_spec):
    """Recompute and validate a rebind candidate using trusted registry/parent."""
    if not isinstance(migration_spec, dict) or set(migration_spec) != {
            'version', 'expected_parent', 'slug', 'retired_slug', 'old_repository',
            'old_branch', 'new_repository', 'new_branch'}:
        raise RecoveryError('invalid rebind migration specification')
    if type(migration_spec['version']) is not int or migration_spec['version'] != SPEC_VERSION:
        raise RecoveryError('unsupported rebind migration specification')
    if not isinstance(candidate, ArchiveSnapshot) or not isinstance(previous, ArchiveSnapshot):
        raise RecoveryError('rebind admission requires validated archive snapshots')
    if (migration_spec['expected_parent'] != previous.commit
            or candidate.commit != previous.commit):
        raise RecoveryError('rebind candidate or specification has the wrong archive parent')
    configured, target, old, expected_spec = _migration(
        previous, registry, migration_spec['slug'], migration_spec['old_repository'],
        migration_spec['old_branch'])
    if migration_spec != expected_spec:
        raise RecoveryError('rebind identity differs from the trusted registry or parent')
    expected = _expected_groups(previous, configured, target, old, expected_spec)
    if candidate.groups != expected:
        raise RecoveryError('rebind candidate differs from the independently derived migration')

    # Verify bytes directly as well as through manifest digests, especially for
    # the retired identity whose source repository may already be unavailable.
    for old_slug, entry in previous.groups.items():
        if entry['snapshot'] is None:
            continue
        target_slug = expected_spec['retired_slug'] if old_slug == expected_spec['slug'] else old_slug
        for name in entry['snapshot']['files']:
            if candidate.read_file(target_slug, name) != previous.read_file(old_slug, name):
                raise RecoveryError(f'{target_slug}/{name}: accepted bytes changed during rebind')

    candidate.verify()
    return projected_site(candidate.folder)


def _read_spec(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SPEC_BYTES:
        raise RecoveryError('missing or oversized rebind specification')
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=unique_keys)
    except (ValueError, TypeError) as error:
        raise RecoveryError('invalid rebind specification JSON') from error
    if not isinstance(value, dict):
        raise RecoveryError('invalid rebind specification')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='build a local rebind candidate')
    prepare.add_argument('--archive-snapshot', required=True, help='validated current parent snapshot directory')
    prepare.add_argument('--archive-commit', required=True, help='exact current archive parent commit')
    prepare.add_argument('--registry', required=True, help='trusted groups.json')
    prepare.add_argument('--slug', required=True, help='active group slug to rebind')
    prepare.add_argument('--old-repository', required=True)
    prepare.add_argument('--old-branch', required=True)
    prepare.add_argument('--out', required=True, help='new output directory for candidate and spec')

    validate = commands.add_parser('validate', help='independently admit a prepared local candidate')
    validate.add_argument('--archive-snapshot', required=True, help='prepared candidate snapshot directory')
    validate.add_argument('--parent-snapshot', required=True, help='exact expected archive parent snapshot')
    validate.add_argument('--archive-commit', required=True, help='same exact parent commit used at prepare')
    validate.add_argument('--registry', required=True, help='trusted groups.json')
    validate.add_argument('--spec', required=True, help='rebind.json emitted by prepare')
    args = parser.parse_args(argv)

    registry_path = Path(args.registry)
    if registry_path.is_symlink() or not registry_path.is_file():
        raise RecoveryError('registry must be a regular file')
    registry = load_registry(registry_path)

    if args.command == 'prepare':
        output = Path(args.out)
        if output.exists() or output.is_symlink():
            raise RecoveryError('rebind output directory must be new')
        if not output.parent.is_dir():
            raise RecoveryError('rebind output parent must already exist')
        previous = ArchiveSnapshot(args.archive_snapshot, args.archive_commit)
        with tempfile.TemporaryDirectory(prefix='ddd-rebind-cli-', dir=output.parent) as temporary:
            staged_output = Path(temporary) / 'output'
            staged_output.mkdir()
            with redirect_stdout(sys.stderr):
                _, spec = prepare_rebind(previous, registry, args.slug, args.old_repository,
                                         args.old_branch, staged_output / 'candidate')
            (staged_output / 'rebind.json').write_bytes(canonical(spec))
            staged_output.rename(output)
        print(canonical(spec).decode('utf-8'), end='')
        return

    previous = ArchiveSnapshot(args.parent_snapshot, args.archive_commit)
    candidate = ArchiveSnapshot(args.archive_snapshot, args.archive_commit)
    spec = _read_spec(args.spec)
    with redirect_stdout(sys.stderr):
        site = admit_rebind(candidate, registry, previous, spec)
    print(canonical({'valid': True, 'spec': spec, 'projected_site': site}).decode('utf-8'), end='')


if __name__ == '__main__':
    main()
