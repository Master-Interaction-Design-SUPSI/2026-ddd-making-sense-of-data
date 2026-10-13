"""Prepare the static site and discover entries from folders at the repo root."""

import argparse
import hashlib
import json
import shutil
import tempfile
import warnings
from pathlib import Path

from content_contract import validate_visual
from group_registry import SITE_DIRECTORIES, load_registry
from import_groups import import_groups
from publication_state import MANIFEST, PublishedSite, encode_manifest, select_publication


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "_site"
VISUAL_NAME = "visual.svg"


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
        for name in entries:
            shutil.copytree(sources[name], candidate / name)
        (candidate / "entries.json").write_text(json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8")
        if snapshots is not None:
            (candidate / MANIFEST).write_bytes(encode_manifest(snapshots))
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


def main(import_remote=False, client_factory=None, report_path=None, published_url=None, first_publication=False,
         expected_manifest_sha256=None):
    groups = load_registry(ROOT / "groups.json")
    sources = {name: ROOT / name for name in find_entries()}
    if not import_remote:
        if published_url or first_publication:
            raise ValueError("Recovery options require --import-groups")
        write_site(sources)
        return
    for group in groups:
        if (ROOT / group.slug).exists() or (ROOT / group.slug).is_symlink():
            raise ValueError(f"{group.slug}: registered group collides with a local path; migrate it explicitly first")
    if bool(published_url) == bool(first_publication):
        raise ValueError("Choose --published-url for recovery or --first-publication for an explicit initial build")
    report = Path(report_path) if report_path else ROOT / ".scratch" / "group-import-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    outcome = {"build_status": "failed", "groups": []}
    try:
        previous = PublishedSite(published_url) if published_url else None
        if expected_manifest_sha256 and (previous is None or
                hashlib.sha256(previous.payload).hexdigest() != expected_manifest_sha256):
            raise RuntimeError("Published manifest no longer matches the verified deployment; retry later")
        with tempfile.TemporaryDirectory(prefix="ddd-group-import-") as temporary:
            destination = Path(temporary)
            options = {"client_factory": client_factory} if client_factory else {}
            results = import_groups(groups, destination, **options)
            outcome["groups"] = results
            selected, snapshots = select_publication(results, destination, previous, destination / ".retained")
            sources.update(selected)
            write_site(sources, snapshots)
            outcome["build_status"] = "prepared"
    except Exception as error:
        outcome["error"] = str(error)
        raise
    finally:
        report.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(f"{result['slug']}: {result['publication_status']}" + (f" — {result['error']}" if "error" in result else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-groups", action="store_true", help="Fetch enabled public group repositories before building")
    parser.add_argument("--report", type=Path, help="Import report path (default: .scratch/group-import-report.json)")
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--published-url", help="Canonical HTTPS site root containing the last deployed publication.json")
    recovery.add_argument("--first-publication", action="store_true", help="Explicitly build without previous group state (initial rollout or disposable preview only)")
    arguments = parser.parse_args()
    main(import_remote=arguments.import_groups, report_path=arguments.report,
         published_url=arguments.published_url, first_publication=arguments.first_publication)
