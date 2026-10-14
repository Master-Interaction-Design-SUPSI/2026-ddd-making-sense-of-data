"""Rehearse migration and rollback with two disposable content repositories."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import build_site
from content_contract import select_content_files
from publication_state import MANIFEST, decode_manifest
from test_import_groups import FixtureAPI
from test_publication_state import PublishedHTTP


COURSE_ROOT = Path(__file__).resolve().parent.parent


def file_bytes(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()}


class PilotMigrationTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.root = self.workspace / "checkout"
        self.root.mkdir()
        self.output = self.root / "_site"
        self.live = self.workspace / "published"
        self.live.mkdir()
        self.backup = self.workspace / "original-sources"
        self.backup.mkdir()
        shutil.copy2(COURSE_ROOT / "index.html", self.root / "index.html")
        shutil.copytree(COURSE_ROOT / "static", self.root / "static")
        assets = Path(__file__).resolve().parent.parent
        shutil.copytree(assets / "editor", self.root / "editor")
        shutil.copytree(assets / "templates", self.root / "templates")
        self.registry()
        self.api = FixtureAPI()
        for module, attribute, value in (
            (build_site, "ROOT", self.root),
            (build_site, "OUTPUT", self.output),
        ):
            patcher = patch.object(module, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("publication_state.build_opener", return_value=PublishedHTTP(self.live))
        patcher.start()
        self.addCleanup(patcher.stop)

    def registry(self, *names):
        groups = [{"slug": name, "repository": "pilot/" + name, "enabled": True} for name in names]
        (self.root / "groups.json").write_text(json.dumps({"groups": groups}))

    def upload(self, name, files):
        repository = "pilot/" + name
        old_commit, _ = self.api.add(repository, files)
        revision = hashlib.sha1(repository.encode() + b"".join(
            path.encode() + payload for path, payload in sorted(files.items()))).hexdigest()
        base = "/repos/" + repository
        commit = self.api.routes.pop(base + "/git/commits/" + old_commit)
        commit["sha"] = revision
        self.api.routes[base + "/git/commits/" + revision] = commit
        self.api.routes[base + "/git/ref/heads/main"]["object"]["sha"] = revision

    def refresh(self, first=False):
        build_site.main(import_remote=True, client_factory=self.api.client,
                        first_publication=first,
                        published_url=None if first else "https://site.test/course/")

    def deploy(self):
        shutil.rmtree(self.live)
        shutil.copytree(self.output, self.live)

    def statuses(self):
        report = json.loads((self.root / ".scratch/group-import-report.json").read_text())
        self.assertEqual(report["build_status"], "prepared")
        return {group["slug"]: group["publication_status"] for group in report["groups"]}

    def test_two_group_migration_refresh_unpublishing_and_exact_rollback(self):
        template = file_bytes(COURSE_ROOT / "templates/group")
        selected = select_content_files({name: len(data) for name, data in template.items()})
        original = {name: template[name] for name in selected}
        for name in ("alpha", "beta"):
            folder = self.root / name
            folder.mkdir()
            for path, data in original.items():
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            shutil.copytree(folder, self.backup / name)
            self.upload(name, original)
        build_site.main()
        self.deploy()
        old_site = file_bytes(self.live)
        self.registry("alpha", "beta")
        with self.assertRaisesRegex(ValueError, "collides"):
            self.refresh(first=True)
        self.assertEqual(file_bytes(self.live), old_site)
        self.assertEqual(self.api.calls, [])

        # Retire only disposable checkout copies; original sources stay outside it.
        for name in ("alpha", "beta"):
            shutil.rmtree(self.root / name)
        self.refresh(first=True)
        self.assertEqual(json.loads((self.output / "entries.json").read_text()), ["alpha", "beta"])
        for name in ("alpha", "beta"):
            self.assertEqual(file_bytes(self.output / name), original)
        self.deploy()

        changed = {**original, "text.md": original["text.md"] + b"\nA browser edit.\n"}
        self.upload("alpha", changed)
        self.upload("beta", {**changed, "visual.svg": b"broken upload"})
        # Remove runner output and reports: fallback must come from the served copy.
        shutil.rmtree(self.output)
        shutil.rmtree(self.root / ".scratch")
        self.refresh()
        self.assertEqual(self.statuses(), {"alpha": "updated", "beta": "retained"})
        self.assertEqual(file_bytes(self.output / "alpha"), changed)
        self.assertEqual(file_bytes(self.output / "beta"), original)
        self.deploy()

        self.upload("beta", changed)
        self.refresh()
        self.assertEqual(file_bytes(self.output / "beta"), changed)
        self.deploy()
        self.api.routes["/repos/pilot/alpha"] = URLError("temporary pilot outage")
        with patch("github_content.time.sleep"):
            self.refresh()
        self.assertEqual(self.statuses(), {"alpha": "retained", "beta": "updated"})
        self.assertEqual(file_bytes(self.output / "alpha"), changed)
        self.deploy()

        self.registry("alpha")
        self.refresh()
        self.assertFalse((self.output / "beta").exists())
        self.assertEqual(set(decode_manifest((self.output / MANIFEST).read_bytes())), {"alpha"})
        self.deploy()

        self.registry()
        for name in ("alpha", "beta"):
            self.assertEqual(file_bytes(self.backup / name), original)
            shutil.copytree(self.backup / name, self.root / name)
        build_site.main()
        self.deploy()
        self.assertEqual(file_bytes(self.live), old_site)

    def test_legacy_raster_entry_stops_migration_without_replacing_previous_site(self):
        folder = self.root / "legacy"
        folder.mkdir()
        (folder / "text.md").write_text("Preserve this entry")
        (folder / "visual.png").write_bytes(b"original raster artwork")
        self.output.mkdir()
        (self.output / "index.html").write_text("previous published site")
        previous = file_bytes(self.output)
        source = file_bytes(folder)
        self.registry("legacy")
        with self.assertRaisesRegex(ValueError, "exactly one visual.svg"):
            self.refresh(first=True)
        self.assertEqual(file_bytes(self.output), previous)
        self.assertEqual(file_bytes(folder), source)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
