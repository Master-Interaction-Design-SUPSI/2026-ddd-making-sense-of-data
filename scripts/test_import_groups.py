import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import build_site
from content_contract import MAX_MEDIA_BYTES
from github_content import GitHubClient, ImportFailure
from group_registry import Group
from import_groups import import_groups, validate_references


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="10" height="10"/></svg>'
TEXT = b'---\ntitle: Fixture\nauthors: Test group\n---\n*Italic* and {crimson|color}. [[Detail|media/detail.svg]]'


class Response(io.BytesIO):
    headers = {}


class FixtureAPI:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, repository, files=None, branch="main"):
        files = files or {"text.md": TEXT, "visual.svg": SVG, "media/detail.svg": SVG}
        base = "/repos/" + repository
        commit = hashlib.sha1(repository.encode()).hexdigest()
        tree = hashlib.sha1((repository + "tree").encode()).hexdigest()
        self.routes[base] = dict(private=False, visibility="public", full_name=repository)
        self.routes[base + "/git/ref/heads/" + branch] = {"ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": commit}}
        self.routes[base + "/git/commits/" + commit] = {"sha": commit, "tree": {"sha": tree}}
        nodes = []
        for name, data in files.items():
            sha = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            nodes.append(dict(path=name, mode="100644", type="blob", size=len(data), sha=sha))
            self.routes[base + "/git/blobs/" + sha] = data
        self.routes[base + "/git/trees/" + tree + "?recursive=1"] = {"sha": tree, "tree": nodes, "truncated": False}
        return commit, nodes

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        path = url.path + ("?" + url.query if url.query else "")
        self.calls.append(path)
        if path not in self.routes:
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        data = self.routes[path]
        if isinstance(data, Exception):
            raise data
        return Response(data if isinstance(data, bytes) else json.dumps(data).encode())

    def client(self):
        client = GitHubClient()
        client.opener = self
        return client


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api = FixtureAPI()

    def group(self, slug="alpha", enabled=True, branch="main"):
        return Group(slug, "course/" + slug, branch, enabled)

    def run_import(self, groups=None):
        return import_groups(groups or [self.group()], self.root / "imports", self.api.client)

    def test_two_repositories_import_only_selected_bytes_from_pinned_commits(self):
        files = {"text.md": TEXT + b' [[Listen|media/sound.mp3]] [[Watch|media/movie.mp4]]',
                 "visual.svg": SVG, "media/detail.svg": SVG,
                 "media/sound.mp3": b'ID3 audio fixture', "media/movie.mp4": b'video fixture',
                 "README.md": b'ignored', "index.html": b'<script>bad()</script>',
                 ".github/workflows/run.yml": b'run: bad-command', "media/script.js": b'bad()'}
        alpha, nodes = self.api.add("course/alpha", files, branch="draft/content")
        beta, _ = self.api.add("course/beta")
        self.api.add("course/unregistered")
        result = self.run_import([self.group(branch="draft/content"), self.group("beta"), self.group("disabled", False)])
        self.assertEqual([r["status"] for r in result], ["imported", "imported", "disabled"])
        self.assertEqual([r["commit"] for r in result], [alpha, beta, None])
        for name in result[0]["files"]:
            self.assertEqual((self.root / "imports/alpha" / name).read_bytes(), files[name])
        self.assertEqual(len(list((self.root / "imports/alpha").rglob("*.js"))), 0)
        self.assertFalse((self.root / "imports/alpha/README.md").exists())
        self.assertFalse(any("unregistered" in call or "disabled" in call for call in self.api.calls))
        self.assertEqual(self.api.calls.count("/repos/course/alpha/git/ref/heads/draft/content"), 1)
        self.assertIn("/repos/course/alpha/git/commits/" + alpha, self.api.calls)
        ignored = {n["sha"] for n in nodes if n["path"] not in result[0]["files"]}
        self.assertFalse(any(call.rsplit("/", 1)[-1] in ignored for call in self.api.calls))

    def test_one_failure_does_not_stop_other_groups_or_leave_partial_files(self):
        commit, _ = self.api.add("course/alpha", {"text.md": b'[[missing|media/absent.svg]]', "visual.svg": SVG})
        self.api.add("course/beta")
        results = self.run_import([self.group(), self.group("beta")])
        self.assertEqual(results[0]["error_code"], "invalid_content")
        self.assertEqual(results[0]["commit"], commit)
        self.assertEqual(results[1]["status"], "imported")
        self.assertEqual([p.name for p in (self.root / "imports").iterdir()], ["beta"])

    def test_rejects_private_and_missing_repositories_and_missing_branch(self):
        self.api.add("course/private")
        self.api.routes["/repos/course/private"].update(private=True, visibility="private")
        self.api.add("course/alpha")
        results = self.run_import([self.group("private"), self.group("missing"), self.group(branch="absent")])
        self.assertEqual([r["error_code"] for r in results], ["non_public", "unavailable_repository", "missing_branch"])
        self.assertEqual(sum("private" in call for call in self.api.calls), 1)

    def test_rejects_unsafe_tree_before_downloading_any_files(self):
        for mutation in ("traversal", "symlink", "submodule", "oversize", "duplicate", "parent-file", "truncated", "too-many"):
            with self.subTest(mutation=mutation):
                self.api = FixtureAPI()
                _, nodes = self.api.add("course/alpha")
                tree = next(value for key, value in self.api.routes.items() if "/git/trees/" in key)
                if mutation == "traversal":
                    nodes[0]["path"] = "../escaped.md"
                elif mutation == "symlink":
                    nodes[0]["mode"] = "120000"
                elif mutation == "submodule":
                    nodes[0].update(mode="160000", type="commit")
                elif mutation == "oversize":
                    nodes.append(dict(path="media/large.mp4", size=MAX_MEDIA_BYTES + 1, type="blob", mode="100644", sha="a" * 40))
                elif mutation == "duplicate":
                    nodes.append(nodes[0].copy())
                elif mutation == "parent-file":
                    nodes.append({**nodes[0], "path": "media"})
                elif mutation == "truncated":
                    tree["truncated"] = True
                else:
                    tree["tree"] = nodes * 1000
                result = self.run_import()[0]
                self.assertEqual(result["status"], "failed")
                self.assertFalse(any("/git/blobs/" in call for call in self.api.calls))
                self.assertFalse((self.root / "escaped.md").exists())

    def test_rejects_malformed_text_main_svg_active_svg_and_lfs_pointers(self):
        for name, data in (
            ("text.md", b'\xff'),
            ("visual.svg", b'not SVG'),
            ("visual.svg", SVG.replace(b'1920', b'960')),
            ("media/detail.svg", SVG.replace(b'<rect', b'<script>bad()</script><rect')),
            ("media/detail.svg", SVG.replace(b'<rect', b'<rect onclick="bad()"')),
            ("media/detail.svg", b'<?xml-stylesheet href="https://bad.test/style.css"?>' + SVG),
            ("media/detail.svg", b'version https://git-lfs.github.com/spec/v1\noid sha256:fake'),
        ):
            with self.subTest(name=name, data=data):
                self.api = FixtureAPI()
                self.api.add("course/alpha", {"text.md": TEXT, "visual.svg": SVG, "media/detail.svg": SVG, name: data})
                result = self.run_import()[0]
                self.assertEqual(result["error_code"], "invalid_content")
                self.assertFalse((self.root / "imports/alpha").exists())

    def test_blob_content_cannot_change_after_the_branch_is_resolved(self):
        _, nodes = self.api.add("course/alpha")
        self.api.routes["/repos/course/alpha/git/blobs/" + nodes[0]["sha"]] = b'x' * nodes[0]["size"]
        self.assertEqual(self.run_import()[0]["error_code"], "invalid_blob")

    def test_existing_destination_is_never_overwritten(self):
        folder = self.root / "imports/alpha"
        folder.mkdir(parents=True)
        (folder / "keep.txt").write_text("original")
        self.assertEqual(self.run_import()[0]["error_code"], "invalid_content")
        self.assertEqual((folder / "keep.txt").read_text(), "original")
        self.assertEqual(self.api.calls, [])

    def test_malformed_api_response_is_reported_without_stopping_other_groups(self):
        self.api.routes["/repos/course/alpha"] = b'not JSON'
        self.api.add("course/beta")
        results = self.run_import([self.group(), self.group("beta")])
        self.assertEqual(results[0]["error_code"], "invalid_response")
        self.assertEqual(results[1]["status"], "imported")

    def test_text_parser_failure_stops_publication_as_a_trusted_code_error(self):
        self.api.add("course/alpha")
        with patch("import_groups.subprocess.run", side_effect=subprocess.CalledProcessError(1, "node")):
            with self.assertRaisesRegex(RuntimeError, "Text validator failed"):
                self.run_import()

    def test_static_svg_can_use_local_gradients_and_embedded_raster_images(self):
        svg = SVG.replace(b'<rect width="10" height="10"/>', b'''<defs><linearGradient id="paint"><stop offset="0" stop-color="red"/></linearGradient></defs><rect width="10" height="10" fill="url('#paint')"/><image href="data:image/png;base64,aGVsbG8="/>''')
        self.api.add("course/alpha", {"text.md": TEXT, "visual.svg": svg, "media/detail.svg": svg})
        self.assertEqual(self.run_import()[0]["status"], "imported")

    def test_reference_markdown_links_and_annotations_use_group_local_files(self):
        valid = "![chart][img]\n\n[img]: media/detail.svg\n\n[[detail|./media/detail.svg]] [source](https://example.org) [jump](#note)"
        validate_references(valid, {"media/detail.svg"})
        for target in ("../other/visual.svg", "%2e%2e/other/visual.svg", "/static/main.js", "//other.test/photo.jpg", "media/missing.svg", "javascript:alert"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_references(f"[link]({target})", {"media/detail.svg"})

    def prepare_build(self, groups):
        (self.root / "groups.json").write_text(json.dumps({"groups": [vars(g) for g in groups]}))
        (self.root / "index.html").write_text("trusted site")
        (self.root / "static").mkdir()
        output = self.root / "_site"
        output.mkdir()
        (output / "index.html").write_text("previous publication")
        return output

    def test_build_imports_two_groups_and_writes_source_report(self):
        self.api.add("course/alpha")
        self.api.add("course/beta")
        output = self.prepare_build([self.group(), self.group("beta")])
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            build_site.main(import_remote=True, client_factory=self.api.client, first_publication=True)
        self.assertEqual(json.loads((output / "entries.json").read_text()), ["alpha", "beta"])
        self.assertEqual((output / "alpha/media/detail.svg").read_bytes(), SVG)
        self.assertEqual((output / "index.html").read_text(), "trusted site")
        self.assertFalse((self.root / "alpha").exists())
        report = json.loads((self.root / ".scratch/group-import-report.json").read_text())
        self.assertEqual([g["status"] for g in report["groups"]], ["imported", "imported"])

    def test_first_publication_skips_invalid_group_after_fetching_other_groups(self):
        self.api.add("course/beta")
        output = self.prepare_build([self.group(), self.group("beta")])
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            build_site.main(import_remote=True, client_factory=self.api.client, first_publication=True)
        self.assertEqual(json.loads((output / "entries.json").read_text()), ["beta"])
        report = json.loads((self.root / ".scratch/group-import-report.json").read_text())
        self.assertEqual([g["status"] for g in report["groups"]], ["failed", "imported"])
        self.assertEqual([g["publication_status"] for g in report["groups"]], ["skipped", "updated"])

    def test_local_path_collision_is_rejected_before_network_or_build_changes(self):
        output = self.prepare_build([self.group()])
        (self.root / "alpha").mkdir()
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            with self.assertRaisesRegex(ValueError, "collides"):
                build_site.main(import_remote=True, client_factory=self.api.client)
        self.assertEqual(self.api.calls, [])
        self.assertEqual((output / "index.html").read_text(), "previous publication")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubClient()
        self.client.opener = unittest.mock.Mock()

    def test_rate_limit_is_distinct_and_not_retried(self):
        self.client.opener.open.side_effect = HTTPError("https://api.github.com/repos/course/test", 403, "limit", {"X-RateLimit-Remaining": "0"}, None)
        with self.assertRaises(ImportFailure) as caught:
            self.client.get("/repos/course/test", 100)
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_network_retries_are_bounded(self):
        self.client.opener.open.side_effect = URLError("offline")
        with patch("github_content.time.sleep") as sleep, self.assertRaises(ImportFailure) as caught:
            self.client.get("/repos/course/test", 100)
        self.assertEqual(caught.exception.code, "network_error")
        self.assertEqual(self.client.opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_transient_server_failure_recovers(self):
        self.client.opener.open.side_effect = [HTTPError("url", 503, "unavailable", {}, None), Response(b'{}')]
        with patch("github_content.time.sleep"):
            self.assertEqual(self.client.json("/repos/course/test"), {})

    def test_response_limits_apply_without_content_length(self):
        self.client.opener.open.return_value = Response(b'too large')
        with self.assertRaises(ImportFailure) as caught:
            self.client.get("/repos/course/test", 2)
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_oversized_content_length_is_rejected_before_body_read(self):
        response = Response(b'small')
        response.headers = {"Content-Length": "1000000"}
        self.client.opener.open.return_value = response
        with patch.object(response, "read1") as read, self.assertRaises(ImportFailure):
            self.client.get("/repos/course/test", 100)
        read.assert_not_called()

    def test_deadline_and_redirects_do_not_make_unbounded_requests(self):
        self.client.deadline = 0
        with self.assertRaises(ImportFailure) as caught:
            self.client.get("/repos/course/test", 100)
        self.assertEqual(caught.exception.code, "timeout")
        self.client.opener.open.assert_not_called()
        self.client = GitHubClient()
        self.client.opener = unittest.mock.Mock()
        self.client.opener.open.side_effect = HTTPError("url", 302, "redirect", {"Location": "https://other.test"}, None)
        with self.assertRaises(ImportFailure) as caught:
            self.client.get("/repos/course/test", 100)
        self.assertEqual(caught.exception.code, "redirect")
        self.assertEqual(self.client.opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
