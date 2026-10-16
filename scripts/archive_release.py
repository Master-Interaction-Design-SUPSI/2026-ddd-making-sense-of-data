"""Release pairs and exact reconstruction from durable main/archive identities."""

import hashlib

import build_site
from content_contract import MEDIA_TABLE
from archive_state import MAX_MANIFEST_BYTES, decode_archive, served_groups
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
    # media.csv is probed only when no real media file changed.
    media.sort(key=lambda path: path.endswith('/' + MEDIA_TABLE))
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


def verified_previous_groups(records, repository, verified_record):
    """Read the served inventory of the last verified release from its immutable archive pin."""
    if not verified_record:
        return None
    release = validate_release(verified_record['payload'])
    payload = records.client.raw_file(repository, release['archive_commit'], 'publication.json',
                                      MAX_MANIFEST_BYTES)
    if hashlib.sha256(payload).hexdigest() != release['archive_manifest_sha256']:
        raise RecoveryError('previous archive manifest differs from verified release pin')
    return served_groups(decode_archive(payload))


def reconstruct(release, archive, main_commit, main_tree):
    """Use matching trusted checkout and pinned archive, even after artifact expiration."""
    validate_release(release)
    if (main_commit != release['main_commit'] or main_tree != release['main_tree']
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
        return {'mode': 'refresh'}
    release = validate_release(latest['payload'])
    latest_verified = bool(verified and verified['id'] == latest['id'])
    if latest_verified and verified['payload'] != release:
        raise RecoveryError('verified record payload differs from latest attempted release')
    if latest_verified or release['main_commit'] != main_commit:
        return {'mode': 'refresh'}
    return {'mode': 'verify_then_retry', 'deployment_id': latest['id'], 'release': release}
