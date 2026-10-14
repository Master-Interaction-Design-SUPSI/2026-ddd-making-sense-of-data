"""Release pairs and exact reconstruction from durable main/archive identities."""

import hashlib

import build_site
from archive_state import ArchiveSnapshot
from publication_state import MANIFEST, RecoveryError
from publish_site import RELEASE, encode, site_digest, validate_release


def probes_for(groups, previous=None):
    """Probe changed text plus one representative changed media file per release."""
    previous = previous or {}
    text, media = [], []
    for slug, entry in sorted(groups.items()):
        old = previous.get(slug, {}).get('files', {})
        for name, record in sorted(entry['files'].items()):
            if old.get(name) == record:
                continue
            (text if name == 'text.md' else media).append(f'{slug}/{name}')
    if len(text) > 15:
        raise RecoveryError('verification probe group limit exceeded')
    return text + media[:1]


def make_release(main_commit, main_tree, archive, run_id, run_attempt, previous_groups=None):
    """Build trusted site inputs with visible archived content; no source/Pages recovery."""
    identities = build_site.build_archive(archive.folder, archive.commit)
    release = validate_release({'version': 2, 'run_id': str(run_id), 'run_attempt': str(run_attempt),
        'main_commit': main_commit, 'main_tree': main_tree, 'archive_commit': archive.commit,
        'archive_manifest_sha256': identities['archive_manifest_sha256'],
        'served_manifest_sha256': identities['served_manifest_sha256'],
        'site_sha256': site_digest(build_site.OUTPUT),
        'probes': probes_for(archive.served(), previous_groups)})
    (build_site.OUTPUT / RELEASE).write_bytes(encode(release))
    build_site.enforce_site_budget(build_site.OUTPUT)
    return release


def reconstruct(release, archive, main_commit, main_tree):
    """Use matching trusted checkout and pinned archive, even after artifact expiration."""
    validate_release(release)
    if (release['version'] != 2 or main_commit != release['main_commit'] or main_tree != release['main_tree']
            or archive.commit != release['archive_commit']
            or hashlib.sha256(archive.payload).hexdigest() != release['archive_manifest_sha256']):
        raise RecoveryError('reconstruction requires the exact recorded main/archive pair')
    build_site.build_archive(archive.folder, archive.commit)
    if (site_digest(build_site.OUTPUT) != release['site_sha256']
            or hashlib.sha256((build_site.OUTPUT / MANIFEST).read_bytes()).hexdigest() != release['served_manifest_sha256']):
        raise RecoveryError('durable reconstruction differs from recorded release')
    (build_site.OUTPUT / RELEASE).write_bytes(encode(release))
    build_site.enforce_site_budget(build_site.OUTPUT)
    return release


def publication_strategy(latest, verified, main_commit):
    """Retry a pending same-code pair before reading new source revisions."""
    if not latest:
        return {'mode': 'refresh', 'supersedes': None}
    release = validate_release(latest['payload'])
    if release['version'] == 1:
        return {'mode': 'migration', 'supersedes': None}
    if release['main_commit'] != main_commit:
        return {'mode': 'refresh', 'supersedes': latest['id']}
    if verified and verified['id'] == latest['id']:
        if verified['payload'] != release:
            raise RecoveryError('verified record payload differs from latest attempted release')
        return {'mode': 'refresh', 'supersedes': None}
    return {'mode': 'verify_then_retry', 'deployment_id': latest['id'], 'release': release,
            'archive_commit': release['archive_commit'], 'supersedes': None}
