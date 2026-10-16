import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import build_site
from publication_state import MANIFEST, PublishedSite, RecoveryError, decode_manifest
from test_import_groups import FixtureAPI, Response, SVG, TABLE, TEXT


class PublishedHTTP:
    """A deployed filesystem served independently of disposable build directories."""

    def __init__(self, folder):
        self.folder = folder
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        path = self.folder / urlsplit(request.full_url).path.removeprefix("/course/")
        if not path.is_file():
            raise HTTPError(request.full_url, 404, "not published", {}, None)
        return Response(path.read_bytes())


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "checkout"
        self.root.mkdir()
        self.output = self.root / "_site"
        self.live = self.workspace / "live"
        self.live.mkdir()
        self.server = PublishedHTTP(self.live)
        self.api = FixtureAPI()
        for target, value in (("ROOT", self.root), ("OUTPUT", self.output)):
            patcher = patch.object(build_site, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("publication_state.build_opener", return_value=self.server)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / "index.html").write_text("trusted code")
        (self.root / "static").mkdir()
        assets = Path(__file__).resolve().parent.parent
        shutil.copytree(assets / "editor", self.root / "editor")
        shutil.copytree(assets / "templates", self.root / "templates")
        self.registry("alpha", "beta")
        for name in ("alpha", "beta"):
            self.api.add("course/" + name)

    def registry(self, *names, disabled=()):
        groups = [{"slug": name, "repository": "course/" + name, "enabled": name not in disabled} for name in names]
        (self.root / "groups.json").write_text(json.dumps({"groups": groups}))

    def build(self, first=False):
        build_site.main(import_remote=True, client_factory=self.api.client,
                        first_publication=first, published_url=None if first else "https://site.test/course/")

    def deploy(self):
        shutil.rmtree(self.live)
        shutil.copytree(self.output, self.live)

    def bootstrap(self):
        self.build(first=True)
        self.deploy()

    def bytes_at(self, folder):
        return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    def report(self):
        return json.loads((self.root / ".scratch/group-import-report.json").read_text())["groups"]

    def update(self, name, text=b"new content", visual=SVG):
        # A text or visual edit keeps the media.csv from the first revision.
        commit, _ = self.api.add("course/" + name, {"text.md": text, "visual.svg": visual, "media.csv": TABLE})
        # Give each fixture revision a distinct source commit, as Git does.
        base = "/repos/course/" + name
        revision = hashlib.sha1(text + visual).hexdigest()
        data = self.api.routes.pop(base + "/git/commits/" + commit)
        data["sha"] = revision
        self.api.routes[base + "/git/commits/" + revision] = data
        self.api.routes[base + "/git/ref/heads/main"]["object"]["sha"] = revision
        return revision

    def test_mixed_success_restores_whole_snapshot_on_clean_runner_then_recovers(self):
        self.bootstrap()
        original_beta = self.bytes_at(self.live / "beta")
        original_state = decode_manifest((self.live / MANIFEST).read_bytes())
        shutil.rmtree(self.output)
        shutil.rmtree(self.root / ".scratch")
        self.update("alpha", b"new alpha")
        self.update("beta", b"must not leak into fallback", b"broken SVG")
        self.build()
        self.assertEqual((self.output / "alpha/text.md").read_bytes(), b"new alpha")
        self.assertEqual(self.bytes_at(self.output / "beta"), original_beta)
        self.assertEqual(decode_manifest((self.output / MANIFEST).read_bytes())["beta"], original_state["beta"])
        self.assertEqual([r["publication_status"] for r in self.report()], ["updated", "retained"])
        self.assertNotEqual(self.report()[1]["commit"], self.report()[1]["selected_source"]["commit"])
        self.deploy()
        self.update("beta", b"repaired beta")
        self.build()
        self.assertEqual((self.output / "beta/text.md").read_bytes(), b"repaired beta")
        self.assertFalse((self.output / "beta/media").exists())
        self.assertEqual(self.report()[1]["publication_status"], "updated")

    def test_fetch_failure_and_incomplete_or_wrong_size_edits_retain_published_bytes(self):
        self.bootstrap()
        original = self.bytes_at(self.live / "beta")
        for failure in ("network", "missing", "dimensions"):
            with self.subTest(failure=failure):
                self.api.add("course/beta")
                if failure == "network":
                    self.api.routes["/repos/course/beta"] = URLError("temporary outage")
                elif failure == "missing":
                    self.api.add("course/beta", {"text.md": b"incomplete edit"})
                else:
                    self.update("beta", visual=SVG.replace(b"1920", b"960"))
                with patch("github_content.time.sleep"):
                    self.build()
                self.assertEqual(self.bytes_at(self.output / "beta"), original)
                self.assertEqual(self.report()[1]["publication_status"], "retained")

    def test_new_invalid_group_is_skipped_with_error_while_existing_groups_publish(self):
        self.bootstrap()
        self.registry("alpha", "beta", "new")
        self.build()
        self.assertEqual(json.loads((self.output / "entries.json").read_text()), ["alpha", "beta"])
        self.assertEqual(self.report()[2]["publication_status"], "skipped")
        self.assertIn("error", self.report()[2])
        self.assertFalse((self.output / "new").exists())

    def test_text_only_group_publishes_and_recovers_after_fetch_failure(self):
        registry = json.loads((self.root / "groups.json").read_text())
        registry["groups"][1]["require_visual"] = False
        (self.root / "groups.json").write_text(json.dumps(registry))
        self.api.add("course/beta", {"text.md": b'Text without a main visual'})
        self.bootstrap()
        entry = decode_manifest((self.live / MANIFEST).read_bytes())["beta"]
        self.assertFalse(entry["require_visual"])
        self.assertEqual(set(entry["files"]), {"text.md", "media.csv"})
        self.assertFalse((self.live / "beta/visual.svg").exists())
        self.api.routes["/repos/course/beta"] = URLError("temporary outage")
        with patch("github_content.time.sleep"):
            self.build()
        self.assertEqual((self.output / "beta/text.md").read_bytes(), b'Text without a main visual')
        self.assertEqual(self.report()[1]["publication_status"], "retained")

    def test_optional_visual_is_validated_when_present(self):
        registry = json.loads((self.root / "groups.json").read_text())
        registry["groups"][1]["require_visual"] = False
        (self.root / "groups.json").write_text(json.dumps(registry))
        self.api.add("course/beta", {"text.md": b'Text', "visual.svg": b'broken SVG'})
        self.build(first=True)
        self.assertEqual(self.report()[1]["publication_status"], "skipped")

    def test_removed_and_disabled_groups_are_unpublished_without_restoring_their_files(self):
        self.bootstrap()
        self.registry("beta", disabled=("beta",))
        self.build()
        self.assertEqual(json.loads((self.output / "entries.json").read_text()), [])
        self.assertEqual(decode_manifest((self.output / MANIFEST).read_bytes()), {})
        self.assertFalse((self.output / "alpha").exists())
        self.assertFalse((self.output / "beta").exists())
        self.assertTrue(all(urlsplit(r.full_url).path.endswith(MANIFEST) for r in self.server.requests))

    def test_missing_or_corrupt_recovery_manifest_stops_even_when_all_imports_are_valid(self):
        self.bootstrap()
        original = self.bytes_at(self.output)
        for payload in (None, b"not JSON", b'{"version":1,"groups":[]}'):
            with self.subTest(payload=payload):
                (self.live / MANIFEST).unlink(missing_ok=True)
                if payload is not None:
                    (self.live / MANIFEST).write_bytes(payload)
                with self.assertRaises(RecoveryError):
                    self.build()
                self.assertEqual(self.bytes_at(self.output), original)
                report = json.loads((self.root / ".scratch/group-import-report.json").read_text())
                self.assertEqual(report["build_status"], "failed")
                self.assertIn("error", report)
                self.assertEqual(report["groups"], [])

    def test_missing_or_changed_fallback_file_stops_instead_of_publishing_a_partial_group(self):
        self.api.add("course/beta", {"text.md": b"Distinct accepted beta text", "visual.svg": SVG})
        self.bootstrap()
        original = self.bytes_at(self.output)
        self.update("beta", visual=b"invalid")
        for payload in (None, b"altered content"):
            with self.subTest(payload=payload):
                (self.live / "beta/text.md").unlink(missing_ok=True)
                if payload is not None:
                    (self.live / "beta/text.md").write_bytes(payload)
                with self.assertRaises(RecoveryError):
                    self.build()
                self.assertEqual(self.bytes_at(self.output), original)

    def test_failed_deployment_does_not_advance_recovery_to_the_unpublished_candidate(self):
        self.bootstrap()
        published = self.bytes_at(self.live)
        self.update("beta", b"candidate that never deploys")
        self.build()
        self.assertNotEqual((self.output / MANIFEST).read_bytes(), (self.live / MANIFEST).read_bytes())
        self.assertEqual(self.bytes_at(self.live), published)
        # Discard the candidate, as a failed deployment followed by a clean runner would.
        shutil.rmtree(self.output)
        self.update("beta", visual=b"invalid next edit")
        self.build()
        self.assertEqual(self.bytes_at(self.output / "beta"), self.bytes_at(self.live / "beta"))

    def test_trusted_parser_and_site_copy_failures_preserve_previous_output_and_live_site(self):
        self.bootstrap()
        original = self.bytes_at(self.output)
        with patch("import_groups.subprocess.run", side_effect=subprocess.CalledProcessError(1, "node")):
            with self.assertRaisesRegex(RuntimeError, "Text validator"):
                self.build()
        self.assertEqual(self.bytes_at(self.output), original)
        (self.root / "index.html").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertEqual(self.bytes_at(self.output), original)
        self.assertEqual(self.bytes_at(self.live), original)

    def test_changed_manifest_during_build_stops_publication(self):
        self.bootstrap()
        original = self.bytes_at(self.output)
        read = self.server.open
        calls = 0

        def changing_manifest(request, timeout):
            nonlocal calls
            calls += 1
            if calls > 1:
                return Response(b'{"version":1,"groups":{}}')
            return read(request, timeout)

        with patch.object(self.server, "open", side_effect=changing_manifest):
            with self.assertRaisesRegex(RecoveryError, "changed during"):
                self.build()
        self.assertEqual(self.bytes_at(self.output), original)

    def test_imports_require_explicit_recovery_or_bootstrap_and_do_not_send_site_credentials(self):
        with self.assertRaisesRegex(ValueError, "Choose --published-url"):
            build_site.main(import_remote=True, client_factory=self.api.client)
        self.assertEqual(self.api.calls, [])
        self.bootstrap()
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            self.build()
        for request in self.server.requests:
            self.assertFalse(request.has_header("Authorization"))
            self.assertIn("recovery=", request.full_url)

    def test_manifest_rejects_unsafe_paths_bad_hashes_and_duplicate_keys(self):
        self.bootstrap()
        original = json.loads((self.live / MANIFEST).read_bytes())
        for mutation in ("traversal", "hash", "version", "size", "unsupported", "slug"):
            manifest = copy.deepcopy(original)
            entry = manifest["groups"]["beta"]
            if mutation == "traversal":
                entry["files"]["../text.md"] = entry["files"].pop("text.md")
            elif mutation == "hash":
                entry["files"]["text.md"]["sha256"] = "bad hash"
            elif mutation == "version":
                manifest["version"] = True
            elif mutation == "size":
                entry["files"]["text.md"]["size"] = 999999999
            elif mutation == "unsupported":
                entry["files"]["script.js"] = entry["files"]["text.md"]
            else:
                manifest["groups"]["../escaped"] = entry
            with self.subTest(mutation=mutation), self.assertRaises(RecoveryError):
                decode_manifest(json.dumps(manifest))
        with self.assertRaises(RecoveryError):
            decode_manifest(b'{"version":1,"groups":{},"groups":{}}')


class RecoveryTransportTests(unittest.TestCase):
    def test_https_canonical_url_and_bounded_manifest_requests(self):
        for url in ("http://site.test/", "https://user:pass@site.test/", "https://site.test/?query=yes"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                PublishedSite(url)
        opener = Mock()
        with patch("publication_state.build_opener", return_value=opener):
            opener.open.side_effect = HTTPError("url", 302, "redirect", {}, None)
            with self.assertRaises(RecoveryError):
                PublishedSite("https://site.test/")
            opener.open.side_effect = None
            opener.open.return_value = Response(b"x" * (2 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(RecoveryError, "byte limit"):
                PublishedSite("https://site.test/")


if __name__ == "__main__":
    unittest.main()
