"""Prepare the static site and discover entries from folders at the repo root."""

import argparse
import hashlib
import json
import shutil
import tempfile
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

from content_contract import CONTRACT_VERSION, MAX_SITE_BYTES, select_content_files
from group_registry import REPOSITORY, SITE_DIRECTORIES, load_registry
from import_groups import validate_content
from release import MANIFEST, encode_manifest
from archive_state import ArchiveSnapshot


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "_site"


def write_editor(candidate):
    shutil.copytree(ROOT / "editor", candidate / "editor")
    shell = (ROOT / "index.html").read_text(encoding="utf-8")
    shell = shell.replace('href="static/', 'href="../static/').replace('src="static/', 'src="../static/')
    shell = shell.replace('<script src="../static/bootstrap.js"></script>',
                          '<script src="protocol.js"></script>\n    <script src="preview.js"></script>')
    # The preview keeps the bar's pixels, but its editor link would point at editor/editor/.
    shell = shell.replace('<a class="bar-link" href="editor/">', '<a class="bar-link">')
    (candidate / "editor" / "preview.html").write_text(shell, encoding="utf-8")
    groups = [{"slug": group.slug, "repository": group.repository, "branch": group.branch,
               "exclude": list(group.exclude), "state": group.state, "editable": group.state == 'live',
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
    """Course-team folders at the root: the checks of an import, but the main visual is optional."""
    entries = []
    for folder in ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in SITE_DIRECTORIES:
            continue
        if not (folder / "text.md").is_file():
            if any(path.is_file() and path.stem.lower() == "visual" for path in folder.iterdir()):
                warnings.warn(f"{folder.name}: waiting for text.md")
            continue
        sizes = {path.relative_to(folder).as_posix(): path.stat().st_size
                 for path in folder.rglob("*") if path.is_file()}
        try:
            # Every group needs the main visual; a course-team folder may leave it out.
            notes = validate_content(folder, select_content_files(sizes, required=("text.md",)))
        except (ValueError, ET.ParseError) as error:
            raise ValueError(f"{folder.name}: {error}") from error
        for note in notes:
            warnings.warn(f"{folder.name}: {note}")
        entries.append(folder.name)
    return sorted(entries, key=lambda name: (name != "example", name.casefold()))


def write_site(sources, snapshots=None, statuses=None):
    """statuses: per group, which commit is online and which was not published; groups
    with only a problem get an entry too, so a first commit that fails is visible."""
    statuses = statuses or {}
    entries = sorted(set(sources) | set(statuses),
                     key=lambda name: (name != "materialities-of-ai", name != "example", name.casefold()))
    with tempfile.TemporaryDirectory(prefix=".site-build-", dir=OUTPUT.parent) as temporary:
        candidate = Path(temporary) / "candidate"
        candidate.mkdir()
        shutil.copy2(ROOT / "index.html", candidate / "index.html")
        shutil.copytree(ROOT / "static", candidate / "static")
        write_editor(candidate)
        listing = []
        for name in entries:
            files = []
            if name in sources:
                shutil.copytree(sources[name], candidate / name)
                # The page marks a referenced file as missing when it is not in this list.
                files = sorted(path.relative_to(candidate / name).as_posix()
                               for path in (candidate / name).rglob("*") if path.is_file())
            listing.append({"slug": name, "files": files, **statuses.get(name, {})})
        (candidate / "entries.json").write_text(json.dumps(listing, ensure_ascii=False) + "\n", encoding="utf-8")
        if snapshots is not None:
            (candidate / MANIFEST).write_bytes(encode_manifest(snapshots))
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


def group_statuses(groups):
    """From the archive alone, so an exact retry rebuilds the same entries.json."""
    statuses = {}
    for slug, entry in groups.items():
        if entry['state'] == 'hidden':
            continue
        status = {}
        if entry['snapshot']:
            status['online'] = {'commit': entry['snapshot']['commit'][:7], 'committed_at': entry['snapshot']['committed_at']}
        if entry['problem'] and entry['state'] == 'live':
            status['problem'] = {'commit': entry['problem']['commit'][:7], 'message': entry['problem']['message']}
        if status:
            statuses[slug] = status
    return statuses


def build_archive(folder, commit=None):
    """Build from archived bytes and trusted current site code; no source or Pages reads."""
    archive = ArchiveSnapshot(folder, commit)
    snapshots = archive.served()
    sources = {name: ROOT / name for name in find_entries()}
    if set(snapshots) & set(sources):
        raise ValueError('archive group collides with a local entry')
    sources.update({slug: archive.folder / slug for slug in snapshots})
    write_site(sources, snapshots, group_statuses(archive.groups))
    return {'archive_commit': archive.commit,
            'archive_manifest_sha256': hashlib.sha256(archive.payload).hexdigest(),
            'served_manifest_sha256': hashlib.sha256((OUTPUT / MANIFEST).read_bytes()).hexdigest()}


def main():
    """Offline preview: local entries only; never deploy it over a publication."""
    for group in load_registry(ROOT / "groups.json"):
        if (ROOT / group.slug).exists() or (ROOT / group.slug).is_symlink():
            raise ValueError(f"{group.slug}: registered group collides with a local path")
    write_site({name: ROOT / name for name in find_entries()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-snapshot', type=Path, help='Build from a verified local archive snapshot (shadow build)')
    parser.add_argument('--archive-commit', help='Exact archive commit identifying --archive-snapshot')
    arguments = parser.parse_args()
    if arguments.archive_snapshot or arguments.archive_commit:
        if not arguments.archive_snapshot or not arguments.archive_commit:
            parser.error('archive snapshot and commit must be supplied together')
        print(json.dumps(build_archive(arguments.archive_snapshot, arguments.archive_commit), sort_keys=True))
    else:
        main()
