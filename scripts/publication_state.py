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

from content_contract import CONTRACT_VERSION, select_content_files
from github_content import NoRedirect
from group_registry import REPOSITORY, RESERVED_SLUGS, SLUG, validate_branch
from import_groups import validate_content


MANIFEST = "publication.json"
SOURCE_INVENTORY = "source-inventory.json"
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
                                            require_visual=require_visual, recovery=True)
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
    entry = {key: result[key] for key in ("repository", "branch", "commit")} | {"files": files}
    if result.get("require_visual") is False:
        entry["require_visual"] = False
    return entry


def encode_source_inventory(sources, snapshots, manifest):
    """Optional reuse hints; the v1 recovery manifest remains authoritative."""
    groups = {}
    for slug, entry in snapshots.items():
        files = {}
        for name, record in entry["files"].items():
            content = (sources[slug] / name).read_bytes()
            if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise RecoveryError(f"{slug}/{name}: source changed while preparing reuse inventory")
            files[name] = {"size": len(content), "blob_sha": hashlib.sha1(
                f"blob {len(content)}\0".encode() + content).hexdigest()}
        groups[slug] = {key: entry[key] for key in ("repository", "branch", "commit")} | {"files": files}
    payload = (json.dumps({"version": 1, "contract_version": CONTRACT_VERSION,
                          "manifest_sha256": hashlib.sha256(manifest).hexdigest(), "groups": groups},
                         sort_keys=True, indent=2) + "\n").encode()
    if len(payload) > MAX_MANIFEST_BYTES:
        raise RecoveryError("Source inventory exceeds the 2 MiB limit")
    return payload


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
        self.overall_deadline = None
        self.metrics = {"pages_requests": 0, "pages_bytes": 0}

    def remaining(self):
        deadline = min(self.deadline, self.overall_deadline) if self.overall_deadline else self.deadline
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryError("Published-state recovery exceeded 180 seconds; retry publication")
        return remaining

    def read(self, path, limit):
        # No GitHub token is ever sent to the public website. Bypass stale CDN responses.
        url = self.url + quote(path, safe="/") + "?recovery=" + str(time.time_ns())
        request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "ddd-publication-recovery"})
        self.metrics["pages_requests"] += 1
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
                    self.metrics["pages_bytes"] += len(chunk)
                    payload.extend(chunk)
                raise RecoveryError(f"Published {path} exceeds its recovery byte limit")
        except (URLError, OSError, HTTPException) as error:
            raise RecoveryError(f"Cannot restore published {path}; check the canonical site URL and retry. {error}") from error


class PublishedSite(SiteReader):
    def __init__(self, url):
        super().__init__(url)
        self.payload = self.read(MANIFEST, MAX_MANIFEST_BYTES)
        self.groups = decode_manifest(self.payload)
        self.inventory = None
        self.inventory_loaded = False
        self.cache_directory = None

    def load_inventory(self):
        if self.inventory_loaded:
            return
        self.inventory_loaded = True
        try:
            inventory = json.loads(self.read(SOURCE_INVENTORY, MAX_MANIFEST_BYTES), object_pairs_hook=unique_keys)
            if (not isinstance(inventory, dict) or inventory.get("version") != 1
                    or inventory.get("contract_version") != CONTRACT_VERSION
                    or inventory.get("manifest_sha256") != hashlib.sha256(self.payload).hexdigest()
                    or not isinstance(inventory.get("groups"), dict)
                    or set(inventory.get("groups", {})) != set(self.groups)):
                return
            for slug, group in inventory["groups"].items():
                base = self.groups[slug]
                if (not isinstance(group, dict) or any(group.get(key) != base[key]
                        for key in ("repository", "branch", "commit"))
                        or not isinstance(group.get("files"), dict) or set(group["files"]) != set(base["files"])):
                    return
                for name, record in group["files"].items():
                    if (not isinstance(record, dict) or record.get("size") != base["files"][name]["size"]
                            or not isinstance(record.get("blob_sha"), str)
                            or not re.fullmatch(r"[0-9a-f]{40}", record["blob_sha"])):
                        return
            self.inventory = inventory["groups"]
        except (RecoveryError, ValueError, TypeError, KeyError, RecursionError):
            # Missing, stale or damaged acceleration metadata cannot become authority.
            return

    def reuse(self, slug, name, node):
        self.load_inventory()
        if self.inventory is None:
            return None
        hint = self.inventory.get(slug, {}).get("files", {}).get(name)
        if not hint or hint["blob_sha"] != node["sha"] or hint["size"] != node["size"]:
            return None
        record = self.groups[slug]["files"][name]
        try:
            content = self.read_file(slug, name)
        except RecoveryError:
            return None
        if (len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]
                or hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest() != node["sha"]):
            return None
        return content

    def read_file(self, slug, name):
        record = self.groups[slug]["files"][name]
        cached = self.cache_directory / record["sha256"] if self.cache_directory else None
        content = None
        if cached and cached.is_file() and not cached.is_symlink() and cached.stat().st_size == record["size"]:
            content = cached.read_bytes()
        if content is None or hashlib.sha256(content).hexdigest() != record["sha256"]:
            content = self.read(f"{slug}/{name}", record["size"])
        if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise RecoveryError(f"{slug}/{name}: published bytes do not match the recovery manifest; retry publication")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(content)
        return content

    def restore(self, slug, destination):
        self.deadline = time.monotonic() + 180
        entry = self.groups[slug]
        destination = Path(destination)
        destination.mkdir()
        for name, record in entry["files"].items():
            content = self.read_file(slug, name)
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            validate_content(destination, entry["files"], require_visual=entry.get("require_visual", True))
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
        elif result["status"] == "unchanged":
            sources[slug] = retained / slug
            snapshots[slug] = previous.restore(slug, sources[slug])
            result["publication_status"] = "unchanged"
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
