"""Prepare the static site and discover entries from folders at the repo root."""

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import warnings
from pathlib import Path

from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES, select_content_files, validate_visual
from group_registry import REPOSITORY, SITE_DIRECTORIES, load_registry
from import_groups import import_groups
from publication_state import (
    MANIFEST, SOURCE_INVENTORY, PublishedSite, encode_manifest, encode_source_inventory, select_publication,
)
from archive_state import ArchiveSnapshot


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "_site"
VISUAL_NAME = "visual.svg"


def write_editor(candidate):
    shutil.copytree(ROOT / "editor", candidate / "editor")
    shell = (ROOT / "index.html").read_text(encoding="utf-8")
    shell = shell.replace('href="static/', 'href="../static/').replace('src="static/', 'src="../static/')
    shell = shell.replace('<script src="../static/bootstrap.js"></script>',
                          '<script src="protocol.js"></script>\n    <script src="preview.js"></script>')
    (candidate / "editor" / "preview.html").write_text(shell, encoding="utf-8")
    groups = [{"slug": group.slug, "repository": group.repository, "branch": group.branch,
               "exclude": list(group.exclude), "require_visual": group.require_visual,
               "state": group.publication_state, "editable": group.publication_state == 'live',
               "contract_version": CONTRACT_VERSION} for group in load_registry(ROOT / "groups.json")]
    requests = {'enabled': False, 'repository': None}
    policy = ROOT / '.github/publishing.json'
    if policy.exists():
        if policy.is_symlink() or policy.stat().st_size > 16384:
            raise ValueError('invalid publishing UI policy')
        settings = json.loads(policy.read_bytes())
        if (not isinstance(settings, dict) or set(settings) != {'version', 'repository', 'requests_enabled'}
                or type(settings['version']) is not int or settings['version'] != 1
                or type(settings['requests_enabled']) is not bool or not isinstance(settings['repository'], str)
                or not REPOSITORY.fullmatch(settings['repository']) or settings['repository'].endswith('.git')):
            raise ValueError('invalid publishing UI policy')
        requests = {'enabled': settings['requests_enabled'], 'repository': settings['repository']}
    (candidate / "editor" / "groups.json").write_text(
        json.dumps({"groups": groups, 'publication_requests': requests}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    template = ROOT / "templates" / "group"
    sizes = {path.relative_to(template).as_posix(): path.stat().st_size
             for path in template.rglob("*") if path.is_file()}
    files = {}
    for name in select_content_files(sizes):
        payload = (template / name).read_bytes()
        destination = candidate / "editor" / "template" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        files[name] = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    identity = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    (candidate / "editor" / "template.json").write_text(
        json.dumps({"files": files, "digest": identity}, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def enforce_site_budget(candidate):
    total = sum(path.stat().st_size for path in candidate.rglob("*") if path.is_file())
    if total > MAX_SITE_BYTES:
        contributors = []
        for path in candidate.iterdir():
            size = (sum(member.stat().st_size for member in path.rglob("*") if member.is_file())
                    if path.is_dir() else path.stat().st_size)
            contributors.append((size, path.name))
        largest = ", ".join(f"{name}: {size} bytes" for size, name in sorted(contributors, reverse=True)[:5])
        raise ValueError(f"Built site exceeds the {MAX_SITE_BYTES}-byte site limit. Largest contributors: {largest}")
    return total


def find_entries():
    entries = []
    for folder in ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in SITE_DIRECTORIES:
            continue
        has_text = (folder / "text.md").is_file()
        visuals = [path.name for path in folder.iterdir() if path.is_file() and path.stem.lower() == "visual"]
        if not has_text and not visuals:
            continue
        if visuals and visuals != [VISUAL_NAME]:
            raise ValueError(f"{folder.name}: use exactly one {VISUAL_NAME}; remove other visual.* files")
        if visuals:
            validate_visual(folder / VISUAL_NAME)
        if not has_text or not visuals:
            warnings.warn(f"{folder.name}: waiting for text.md and {VISUAL_NAME}")
            continue
        entries.append(folder.name)
    return sorted(entries, key=lambda name: (name != "example", name.casefold()))


def write_site(sources, snapshots=None):
    entries = sorted(sources, key=lambda name: (name != "example", name.casefold()))
    with tempfile.TemporaryDirectory(prefix=".site-build-", dir=OUTPUT.parent) as temporary:
        candidate = Path(temporary) / "candidate"
        candidate.mkdir()
        shutil.copy2(ROOT / "index.html", candidate / "index.html")
        shutil.copytree(ROOT / "static", candidate / "static")
        write_editor(candidate)
        for name in entries:
            shutil.copytree(sources[name], candidate / name)
        (candidate / "entries.json").write_text(json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8")
        if snapshots is not None:
            manifest = encode_manifest(snapshots)
            (candidate / MANIFEST).write_bytes(manifest)
            (candidate / SOURCE_INVENTORY).write_bytes(encode_source_inventory(sources, snapshots, manifest))
        enforce_site_budget(candidate)
        backup = Path(temporary) / "previous"
        if OUTPUT.exists():
            OUTPUT.rename(backup)
        try:
            candidate.rename(OUTPUT)
        except OSError:
            if backup.exists():
                backup.rename(OUTPUT)
            raise
    print(f"Prepared {len(entries)} entries: {', '.join(entries)}")


def build_archive(folder, commit):
    """Shadow/recovery build using durable bytes and trusted current site code."""
    archive = ArchiveSnapshot(folder, commit)
    snapshots = archive.served()
    sources = {name: ROOT / name for name in find_entries()}
    if set(snapshots) & set(sources):
        raise ValueError('archive group collides with a local entry')
    sources.update({slug: archive.folder / slug for slug in snapshots})
    write_site(sources, snapshots)
    return {'archive_commit': archive.commit,
            'archive_manifest_sha256': hashlib.sha256(archive.payload).hexdigest(),
            'served_manifest_sha256': hashlib.sha256((OUTPUT / MANIFEST).read_bytes()).hexdigest()}


def main(import_remote=False, client_factory=None, report_path=None, published_url=None, first_publication=False,
         expected_manifest_sha256=None, allow_unchanged=False):
    groups = load_registry(ROOT / "groups.json")
    sources = {name: ROOT / name for name in find_entries()}
    if not import_remote:
        if published_url or first_publication:
            raise ValueError("Recovery options require --import-groups")
        write_site(sources)
        return
    if any(group.publication_state == 'frozen' for group in groups):
        raise ValueError('Frozen groups require archive publication; V1 cannot recover them safely')
    for group in groups:
        if (ROOT / group.slug).exists() or (ROOT / group.slug).is_symlink():
            raise ValueError(f"{group.slug}: registered group collides with a local path; migrate it explicitly first")
    if bool(published_url) == bool(first_publication):
        raise ValueError("Choose --published-url for recovery or --first-publication for an explicit initial build")
    report = Path(report_path) if report_path else ROOT / ".scratch" / "group-import-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    outcome = {"build_status": "failed", "groups": []}
    started = time.monotonic()
    previous = None
    try:
        previous = PublishedSite(published_url) if published_url else None
        if previous:
            previous.overall_deadline = started + 20 * 60
        if expected_manifest_sha256 and (previous is None or
                hashlib.sha256(previous.payload).hexdigest() != expected_manifest_sha256):
            raise RuntimeError("Published manifest no longer matches the verified deployment; retry later")
        with tempfile.TemporaryDirectory(prefix="ddd-group-import-") as temporary:
            destination = Path(temporary)
            if previous:
                previous.cache_directory = destination / ".verified-cache"
            options = {"client_factory": client_factory} if client_factory else {}
            results = import_groups(groups, destination, previous=previous,
                                    allow_unchanged=allow_unchanged, **options)
            outcome["groups"] = results
            unchanged = {result["slug"] for result in results if result["status"] == "unchanged"}
            if (allow_unchanged and previous and unchanged == set(previous.groups)
                    and all(result["status"] in {"unchanged", "disabled"} for result in results)):
                previous.check_unchanged()
                for result in results:
                    result["publication_status"] = result["status"]
                    result["selected_source"] = ({key: previous.groups[result["slug"]][key]
                                                  for key in ("repository", "branch", "commit")}
                                                 if result["slug"] in unchanged else None)
                outcome["build_status"] = "unchanged"
                return outcome
            selected, snapshots = select_publication(results, destination, previous, destination / ".retained")
            sources.update(selected)
            write_site(sources, snapshots)
            outcome["build_status"] = "prepared"
    except Exception as error:
        outcome["error"] = str(error)
        raise
    finally:
        outcome["transport"] = {"elapsed_seconds": round(time.monotonic() - started, 3),
                                **(previous.metrics if previous else {"pages_requests": 0, "pages_bytes": 0})}
        for metric in ("rest_requests", "raw_requests", "rest_bytes", "raw_bytes"):
            outcome["transport"]["source_" + metric] = sum(
                result.get("transport", {}).get(metric, 0) for result in outcome["groups"])
        outcome["transport"]["reused_bytes"] = sum(result.get("reused_bytes", 0) for result in outcome["groups"])
        report.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(f"{result['slug']}: {result['publication_status']}" + (f" — {result['error']}" if "error" in result else ""))
    return outcome


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-groups", action="store_true", help="Fetch enabled public group repositories before building")
    parser.add_argument("--report", type=Path, help="Import report path (default: .scratch/group-import-report.json)")
    parser.add_argument('--archive-snapshot', type=Path, help='Build from a verified local archive snapshot (shadow/recovery)')
    parser.add_argument('--archive-commit', help='Exact archive commit identifying --archive-snapshot')
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--published-url", help="Canonical HTTPS site root containing the last deployed publication.json")
    recovery.add_argument("--first-publication", action="store_true", help="Explicitly build without previous group state (initial rollout or disposable preview only)")
    arguments = parser.parse_args()
    if arguments.archive_snapshot or arguments.archive_commit:
        if (not arguments.archive_snapshot or not arguments.archive_commit or arguments.import_groups
                or arguments.published_url or arguments.first_publication):
            parser.error('archive snapshot/commit must be supplied together without V1 import/recovery options')
        print(json.dumps(build_archive(arguments.archive_snapshot, arguments.archive_commit), sort_keys=True))
    else:
        main(import_remote=arguments.import_groups, report_path=arguments.report,
             published_url=arguments.published_url, first_publication=arguments.first_publication)
