"""Release identity, the served manifest and verification of the deployed site.

A PublicationError means the outcome is uncertain: stop and keep the live site.
"""

import hashlib
import json
import os
import re
import subprocess
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from content_contract import select_content_files
from github_content import NoRedirect
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, validate_branch


ROOT = Path(__file__).resolve().parent.parent
RELEASE = "release.json"
MANIFEST = "publication.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class PublicationError(RuntimeError):
    """The publication outcome is uncertain: stop and keep the live site."""


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def decode_manifest(payload):
    try:
        manifest = json.loads(payload, object_pairs_hook=unique_keys)
        if not isinstance(manifest, dict) or set(manifest) != {"version", "groups"}:
            raise ValueError("expected version and groups")
        if type(manifest["version"]) is not int or manifest["version"] != 1:
            raise ValueError("unsupported manifest version")
        groups = manifest["groups"]
        if not isinstance(groups, dict):
            raise ValueError("expected a group map")
        for slug, entry in groups.items():
            if len(slug) > 63 or not SLUG.fullmatch(slug) or slug in RESERVED_SLUGS:
                raise ValueError("invalid group slug")
            required_keys = {"repository", "branch", "commit", "files"}
            if (not isinstance(entry, dict) or not required_keys <= entry.keys()
                    or entry.keys() - (required_keys | {"require_visual"})):
                raise ValueError(f"{slug}: invalid snapshot record")
            require_visual = entry.get("require_visual", True)
            if type(require_visual) is not bool:
                raise ValueError(f"{slug}: invalid require_visual")
            repository = entry["repository"]
            if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or repository.endswith(".git"):
                raise ValueError(f"{slug}: invalid repository")
            validate_branch(entry["branch"])
            if not isinstance(entry["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
                raise ValueError(f"{slug}: invalid commit")
            files = entry["files"]
            if not isinstance(files, dict):
                raise ValueError(f"{slug}: invalid file map")
            for name, record in files.items():
                if len(name) > 512 or len(name.split("/")) > 16:
                    raise ValueError(f"{slug}: content path too long")
                if not isinstance(record, dict) or set(record) != {"size", "sha256"}:
                    raise ValueError(f"{slug}: invalid file record")
                if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                    raise ValueError(f"{slug}: invalid file digest")
            selected = select_content_files({name: record["size"] for name, record in files.items()},
                                            require_visual=require_visual)
            if set(selected) != set(files):
                raise ValueError(f"{slug}: unsupported files in served manifest")
            if any(parent in files for name in files for parent in (
                "/".join(name.split("/")[:i]) for i in range(1, len(name.split("/")))
            )):
                raise ValueError(f"{slug}: a parent path is a file")
        return groups
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise PublicationError(f"Invalid served manifest: {error}") from error


def encode_manifest(groups):
    payload = (json.dumps({"version": 1, "groups": groups}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise PublicationError("Served manifest exceeds its 2 MiB limit")
    decode_manifest(payload)
    return payload


class SiteReader:
    def __init__(self, url, deadline=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or "\\" in url
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError("The site URL must be the canonical HTTPS site root, without credentials, query or fragment")
        self.url = url.rstrip("/") + "/"
        self.opener = build_opener(NoRedirect())
        self.deadline = deadline or time.monotonic() + 180

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PublicationError("Reading the served site exceeded 180 seconds; retry publication")
        return remaining

    def read(self, path, limit):
        # No GitHub token is ever sent to the public website. Bypass stale CDN responses.
        url = self.url + quote(path, safe="/") + "?fresh=" + str(time.time_ns())
        request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "ddd-publication-check"})
        try:
            with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise PublicationError(f"Served {path} exceeds its byte limit")
                payload = bytearray()
                while len(payload) <= limit:
                    self.remaining()
                    chunk = response.read1(min(65536, limit + 1 - len(payload)))
                    if not chunk:
                        return bytes(payload)
                    payload.extend(chunk)
                raise PublicationError(f"Served {path} exceeds its byte limit")
        except (URLError, OSError, HTTPException) as error:
            raise PublicationError(f"Cannot read served {path}; check the canonical site URL and retry. {error}") from error


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def validate_release(value):
    fields = {'version', 'run_id', 'run_attempt', 'main_commit', 'main_tree', 'archive_commit',
              'archive_manifest_sha256', 'served_manifest_sha256', 'site_sha256', 'probes'}
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 2 or set(value) != fields):
        raise PublicationError("Invalid publication record; do not bypass the freshness check")
    for key in ("run_id", "run_attempt"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[1-9][0-9]*", value[key]):
            raise PublicationError("Invalid publication run identity")
    for key, length in (('main_commit', 40), ('main_tree', 40), ('archive_commit', 40),
                        ('archive_manifest_sha256', 64), ('served_manifest_sha256', 64), ('site_sha256', 64)):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value[key]):
            raise PublicationError("Invalid publication digest: " + key)
    probes = value['probes']
    if (not isinstance(probes, list) or len(probes) > 16
            or any(not isinstance(path, str) or len(path) > 576 or len(path.split('/')) < 2
                   or not all(re.fullmatch(r'[a-z0-9][a-z0-9._-]*', part) for part in path.split('/'))
                   for path in probes) or len(set(probes)) != len(probes)):
        raise PublicationError('Invalid publication content probes')
    if len(encode(value)) > 4096:
        raise PublicationError('Publication record exceeds its served byte limit')
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
                raise PublicationError("Served publication differs from the latest deployment record")
            groups = decode_manifest(manifest)
            for path in expected['probes']:
                slug, name = path.split('/', 1)
                record = groups.get(slug, {}).get('files', {}).get(name)
                if record is None:
                    raise PublicationError('Verification probe is outside the served inventory')
                content = reader.read(path, record['size'])
                if len(content) != record['size'] or digest(content) != record['sha256']:
                    raise PublicationError('Served content probe differs from accepted snapshot: ' + path)
            if json.loads(reader.read(RELEASE, 4096)) != expected:
                raise PublicationError("Served publication changed during verification")
            return groups
        except (PublicationError, ValueError) as failure:
            error = str(failure)
        if attempt + 1 < attempts:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(delay, remaining))
    raise PublicationError(error + "; publication is blocked. See MAINTAINERS.md §11.")


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
        raise PublicationError('DDD_PUBLICATION_PAUSED must be true or false')
    if value == 'true':
        raise PublicationError('Publication is paused; clear DDD_PUBLICATION_PAUSED to resume')
