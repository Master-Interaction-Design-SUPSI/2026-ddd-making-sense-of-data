"""Recover exact group snapshots from the deployed site, never from a candidate build."""

import hashlib
import json
import re
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from content_contract import select_content_files
from github_content import NoRedirect
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, validate_branch
from import_groups import validate_content


MANIFEST = "publication.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class RecoveryError(RuntimeError):
    """Recovery is uncertain: stop publication and keep the existing live site."""


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
            if not isinstance(entry, dict) or set(entry) != {"repository", "branch", "commit", "files"}:
                raise ValueError(f"{slug}: invalid snapshot record")
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
            selected = select_content_files({name: record["size"] for name, record in files.items()})
            if set(selected) != set(files):
                raise ValueError(f"{slug}: unsupported files in recovery state")
            if any(parent in files for name in files for parent in (
                "/".join(name.split("/")[:i]) for i in range(1, len(name.split("/")))
            )):
                raise ValueError(f"{slug}: a parent path is a file")
        return groups
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise RecoveryError(f"Invalid published recovery manifest: {error}") from error


def encode_manifest(groups):
    payload = (json.dumps({"version": 1, "groups": groups}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise RecoveryError("Publication manifest exceeds the 2 MiB recovery limit")
    decode_manifest(payload)
    return payload


def snapshot(folder, result):
    files = {}
    for name in result["files"]:
        content = (folder / name).read_bytes()
        files[name] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    return {key: result[key] for key in ("repository", "branch", "commit")} | {"files": files}


class SiteReader:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or "\\" in url
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError("--published-url must be the canonical HTTPS site root, without credentials, query or fragment")
        self.url = url.rstrip("/") + "/"
        self.opener = build_opener(NoRedirect())
        self.deadline = time.monotonic() + 180

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryError("Published-state recovery exceeded 180 seconds; retry publication")
        return remaining

    def read(self, path, limit):
        # No GitHub token is ever sent to the public website. Bypass stale CDN responses.
        url = self.url + quote(path, safe="/") + "?recovery=" + str(time.time_ns())
        request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "ddd-publication-recovery"})
        try:
            with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise RecoveryError(f"Published {path} exceeds its recovery byte limit")
                payload = bytearray()
                while len(payload) <= limit:
                    self.remaining()
                    chunk = response.read1(min(65536, limit + 1 - len(payload)))
                    if not chunk:
                        return bytes(payload)
                    payload.extend(chunk)
                raise RecoveryError(f"Published {path} exceeds its recovery byte limit")
        except (URLError, OSError, HTTPException) as error:
            raise RecoveryError(f"Cannot restore published {path}; check the canonical site URL and retry. {error}") from error


class PublishedSite(SiteReader):
    def __init__(self, url):
        super().__init__(url)
        self.payload = self.read(MANIFEST, MAX_MANIFEST_BYTES)
        self.groups = decode_manifest(self.payload)

    def restore(self, slug, destination):
        self.deadline = time.monotonic() + 180
        entry = self.groups[slug]
        destination = Path(destination)
        destination.mkdir()
        for name, record in entry["files"].items():
            content = self.read(f"{slug}/{name}", record["size"])
            if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise RecoveryError(f"{slug}/{name}: published bytes do not match the recovery manifest; retry publication")
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            validate_content(destination, entry["files"])
        except ValueError as error:
            raise RecoveryError(f"{slug}: published snapshot fails current content validation: {error}") from error
        return entry

    def check_unchanged(self):
        self.deadline = time.monotonic() + 180
        if self.read(MANIFEST, MAX_MANIFEST_BYTES) != self.payload:
            raise RecoveryError("Published recovery state changed during this build; retry publication")


def select_publication(results, imported, previous, retained):
    sources, snapshots = {}, {}
    retained.mkdir()
    for result in results:
        slug = result["slug"]
        if result["status"] == "imported":
            sources[slug] = imported / slug
            snapshots[slug] = snapshot(sources[slug], result)
            result["publication_status"] = "updated"
        elif result["status"] == "disabled":
            result["publication_status"] = "disabled"
        elif previous and slug in previous.groups:
            sources[slug] = retained / slug
            snapshots[slug] = previous.restore(slug, sources[slug])
            result["publication_status"] = "retained"
        else:
            result["publication_status"] = "skipped"
        result["selected_source"] = ({key: snapshots[slug][key] for key in ("repository", "branch", "commit")}
                                     if slug in snapshots else None)
    if previous:
        previous.check_unchanged()
    return sources, snapshots
