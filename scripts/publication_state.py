"""The served manifest and bounded reads of the deployed site."""

import json
import re
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from content_contract import select_content_files
from github_content import NoRedirect
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, validate_branch


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
        raise RecoveryError(f"Invalid served manifest: {error}") from error


def encode_manifest(groups):
    payload = (json.dumps({"version": 1, "groups": groups}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise RecoveryError("Served manifest exceeds its 2 MiB limit")
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
            raise RecoveryError("Reading the served site exceeded 180 seconds; retry publication")
        return remaining

    def read(self, path, limit):
        # No GitHub token is ever sent to the public website. Bypass stale CDN responses.
        url = self.url + quote(path, safe="/") + "?fresh=" + str(time.time_ns())
        request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "ddd-publication-check"})
        try:
            with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise RecoveryError(f"Served {path} exceeds its byte limit")
                payload = bytearray()
                while len(payload) <= limit:
                    self.remaining()
                    chunk = response.read1(min(65536, limit + 1 - len(payload)))
                    if not chunk:
                        return bytes(payload)
                    payload.extend(chunk)
                raise RecoveryError(f"Served {path} exceeds its byte limit")
        except (URLError, OSError, HTTPException) as error:
            raise RecoveryError(f"Cannot read served {path}; check the canonical site URL and retry. {error}") from error
