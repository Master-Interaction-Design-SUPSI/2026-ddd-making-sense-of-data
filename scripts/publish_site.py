"""Release identity, served verification and shared controller helpers."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from publication_state import MANIFEST, MAX_MANIFEST_BYTES, RecoveryError, SiteReader, decode_manifest


ROOT = Path(__file__).resolve().parent.parent
RELEASE = "release.json"


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def validate_release(value):
    fields = {'version', 'run_id', 'run_attempt', 'main_commit', 'main_tree', 'archive_commit',
              'archive_manifest_sha256', 'served_manifest_sha256', 'site_sha256', 'probes'}
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 2 or set(value) != fields):
        raise RecoveryError("Invalid publication record; do not bypass the freshness check")
    for key in ("run_id", "run_attempt"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[1-9][0-9]*", value[key]):
            raise RecoveryError("Invalid publication run identity")
    for key, length in (('main_commit', 40), ('main_tree', 40), ('archive_commit', 40),
                        ('archive_manifest_sha256', 64), ('served_manifest_sha256', 64), ('site_sha256', 64)):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value[key]):
            raise RecoveryError("Invalid publication digest: " + key)
    probes = value['probes']
    if (not isinstance(probes, list) or len(probes) > 16
            or any(not isinstance(path, str) or len(path) > 576 or len(path.split('/')) < 2
                   or not all(re.fullmatch(r'[a-z0-9][a-z0-9._-]*', part) for part in path.split('/'))
                   for path in probes) or len(set(probes)) != len(probes)):
        raise RecoveryError('Invalid publication content probes')
    if len(encode(value)) > 4096:
        raise RecoveryError('Publication record exceeds its served byte limit')
    return value


def served_release(url, expected, attempts=12, delay=10):
    validate_release(expected)
    error = "Publication is not served yet"
    deadline = time.monotonic() + 180
    for attempt in range(attempts):
        if time.monotonic() >= deadline:
            break
        reader = SiteReader(url, deadline)
        try:
            release = json.loads(reader.read(RELEASE, 4096))
            manifest = reader.read(MANIFEST, MAX_MANIFEST_BYTES)
            if release != expected or digest(manifest) != expected['served_manifest_sha256']:
                raise RecoveryError("Served publication differs from the latest deployment record")
            groups = decode_manifest(manifest)
            for path in expected['probes']:
                slug, name = path.split('/', 1)
                record = groups.get(slug, {}).get('files', {}).get(name)
                if record is None:
                    raise RecoveryError('Verification probe is outside the served inventory')
                content = reader.read(path, record['size'])
                if len(content) != record['size'] or digest(content) != record['sha256']:
                    raise RecoveryError('Served content probe differs from accepted snapshot: ' + path)
            if json.loads(reader.read(RELEASE, 4096)) != expected:
                raise RecoveryError("Served publication changed during verification")
            return groups
        except (RecoveryError, ValueError) as failure:
            error = str(failure)
        if attempt + 1 < attempts:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(delay, remaining))
    raise RecoveryError(error + "; publication is blocked. See MAINTAINERS.md for recovery.")


def site_digest(folder):
    records = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("The publication must not contain symlinks")
        if not path.is_file() or path.relative_to(folder).as_posix() == RELEASE:
            continue
        checksum = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                checksum.update(chunk)
        records.append([path.relative_to(folder).as_posix(), path.stat().st_size, checksum.hexdigest()])
    return digest(encode(records))


def worktree_clean():
    return not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                       cwd=ROOT, text=True).strip()


def output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def require_publication_active():
    value = os.environ.get('DDD_PUBLICATION_PAUSED', '')
    if value not in {'', 'false', 'true'}:
        raise RecoveryError('DDD_PUBLICATION_PAUSED must be true or false')
    if value == 'true':
        raise RecoveryError('Publication is paused; clear DDD_PUBLICATION_PAUSED to resume')
