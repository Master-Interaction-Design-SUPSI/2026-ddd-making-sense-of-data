import shutil
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import build_site
from archive_state import empty_archive
from archive_transaction import prepare_archive
from content_contract import MAX_MEDIA_BYTES
from github_content import GitHubClient, ImportFailure
from group_registry import Group
from import_groups import import_groups, validate_references
from test_fixtures import FixtureAPI, Response, SVG, TEXT, with_table


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api = FixtureAPI()

    def group(self, slug="alpha", enabled=True, branch="main"):
        return Group(slug, "course/" + slug, branch, "live" if enabled else "hidden")

    def run_import(self, groups=None):
        return import_groups(groups or [self.group()], self.root / "imports", self.api.client)

    def test_two_repositories_import_only_selected_bytes_from_pinned_commits(self):
        files = {"text.md": TEXT + b' [[Listen|media/sound.mp3]] [[Watch|media/movie.mp4]]',
                 "visual.svg": SVG, "media/detail.svg": SVG,
                 "media/sound.mp3": b'ID3 audio fixture', "media/movie.mp4": b'video fixture',
                 "README.md": b'ignored', "index.html": b'<script>bad()</script>',
                 ".github/workflows/run.yml": b'run: bad-command', "media/script.js": b'bad()'}
        files = with_table(files)
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

    def test_public_personal_repositories_import_across_different_owners(self):
        groups = [Group("alpha", "alice-student/project", "main", "live"),
                  Group("beta", "bob-student/project", "main", "live")]
        for group in groups:
            self.api.add(group.repository)
        results = self.run_import(groups)
        self.assertEqual([result["status"] for result in results], ["imported", "imported"])
        self.assertEqual([result["repository"] for result in results],
                         ["alice-student/project", "bob-student/project"])
        self.assertTrue((self.root / "imports/alpha/visual.svg").is_file())
        self.assertTrue((self.root / "imports/beta/visual.svg").is_file())

    def test_excluded_oversized_media_is_never_downloaded(self):
        _, nodes = self.api.add("course/alpha")
        nodes.append(dict(path="media/12.mov", size=MAX_MEDIA_BYTES + 1,
                          type="blob", mode="100644", sha="a" * 40))
        result = self.run_import([Group("alpha", "course/alpha", "main", "live", ("media/12.mov",))])[0]
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["excluded_files"], ["media/12.mov"])
        self.assertNotIn("media/12.mov", result["files"])
        self.assertFalse(any(call.endswith("a" * 40) for call in self.api.calls))

    def test_reference_to_excluded_media_is_accepted_with_a_warning(self):
        self.api.add("course/alpha", {"text.md": TEXT + b' [[Watch|media/12.mov]]',
                                     "visual.svg": SVG, "media/detail.svg": SVG,
                                     "media/12.mov": b'video'})
        result = self.run_import([Group("alpha", "course/alpha", "main", "live", ("media/12.mov",))])[0]
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["warnings"], ["text.md: media/12.mov is not in this group yet"])

    def test_text_committed_before_its_media_is_accepted_and_the_next_commit_completes_it(self):
        files = {"text.md": b"A [[photo]] and ![x](media/later.png).", "visual.svg": SVG,
                 "media.csv": b"name,source,file\nphoto,,media/photo.png\n"}
        self.api.add("course/alpha", files, table=False)
        result = self.run_import()[0]
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["warnings"], ["text.md: media/later.png is not in this group yet",
                                              "text.md: media/photo.png is not in this group yet"])
        self.api.add("course/alpha", {**files, "media/photo.png": b"png", "media/later.png": b"png",
                                      "media.csv": b"name,source,file\nphoto,,media/photo.png\nlater,,media/later.png\n"},
                     table=False, commit="b" * 40)
        self.root = self.root / "next"
        result = self.run_import()[0]
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["warnings"], [])

    def test_inline_playback_template_imports_and_builds_real_media(self):
        template = Path(__file__).resolve().parent.parent / "templates/group"
        files = {path.relative_to(template).as_posix(): path.read_bytes()
                 for path in template.rglob("*") if path.is_file()}
        self.api.add("course/alpha", files)
        output, _ = self.first_archive([self.group()])
        for name in ("text.md", "media/example-motion.mp4", "media/example-tone.mp3"):
            self.assertEqual((output / "alpha" / name).read_bytes(), files[name])
        self.assertFalse((output / "alpha/README.md").exists())

    def test_inline_svg_aspect_ratios_and_raster_survive_import_and_build(self):
        files = {
            "text.md": b'Compare ![wide](media/wide.svg) and [[tall|media/tall.svg]] and ![photo](photo.png).',
            "visual.svg": SVG,
            "media/wide.svg": b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 240"><rect width="600" height="240"/></svg>',
            "media/tall.svg": b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 320"><rect width="160" height="320"/></svg>',
            "photo.png": b'raster fixture',
        }
        self.api.add("course/alpha", files)
        self.assertEqual(self.run_import()[0]["status"], "imported")
        output, _ = self.first_archive([self.group()])
        for name, original in files.items():
            self.assertEqual((output / "alpha" / name).read_bytes(), original)

    def test_one_failure_does_not_stop_other_groups_or_leave_partial_files(self):
        commit, _ = self.api.add("course/alpha", {"text.md": b'[[outside|../beta/visual.svg]]', "visual.svg": SVG})
        self.api.add("course/beta")
        results = self.run_import([self.group(), self.group("beta")])
        self.assertEqual(results[0]["error_code"], "invalid_content")
        self.assertEqual(results[0]["commit"], commit)
        self.assertEqual(results[1]["status"], "imported")
        self.assertEqual([p.name for p in (self.root / "imports").iterdir()], ["beta"])

    def test_media_table_is_required_on_import_and_resolves_names_with_warnings(self):
        table = b'name,source,file\nthe detail,https://example.org/detail,media/detail.svg\n'
        cases = {
            "missing": ({"text.md": TEXT, "visual.svg": SVG, "media/detail.svg": SVG}, "media.csv: required"),
            "header": ({"text.md": TEXT, "visual.svg": SVG, "media.csv": b"file,name\n"}, "media.csv: the first line"),
            "encoding": ({"text.md": TEXT, "visual.svg": SVG, "media.csv": b"name,source,file\n\xe9t\xe9,,media/x.svg\n"},
                         "media.csv: save the table as a UTF-8"),
        }
        for case, (files, message) in cases.items():
            with self.subTest(case=case):
                self.api = FixtureAPI()
                self.api.add("course/alpha", files, table=False)
                result = self.run_import()[0]
                self.assertEqual(result["error_code"], "invalid_content")
                self.assertTrue(result["error"].startswith(message), result["error"])
        warnings = {
            "unknown name": ({"text.md": b"[[elsewhere]]", "visual.svg": SVG, "media.csv": table, "media/detail.svg": SVG},
                             "text.md: [[elsewhere]] is not a name in media.csv"),
            "unlisted media": ({"text.md": TEXT, "visual.svg": SVG, "media.csv": b"name,source,file\n", "media/detail.svg": SVG},
                               "text.md: media/detail.svg is used in the text but has no row in media.csv"),
        }
        for case, (files, message) in warnings.items():
            with self.subTest(case=case):
                self.api = FixtureAPI()
                self.api.add("course/alpha", files, table=False)
                shutil.rmtree(self.root / "imports", ignore_errors=True)
                result = self.run_import()[0]
                self.assertEqual(result["status"], "imported", result.get("error"))
                self.assertEqual(result["warnings"], [message])
        self.api = FixtureAPI()
        self.api.add("course/alpha", {"text.md": b"See [[The Detail]].", "visual.svg": SVG, "media.csv": table,
                                      "media/detail.svg": SVG}, table=False)
        shutil.rmtree(self.root / "imports", ignore_errors=True)
        result = self.run_import()[0]
        self.assertEqual(result["status"], "imported", result.get("error"))
        self.assertEqual(result["warnings"], [])
        self.assertEqual((self.root / "imports/alpha/media.csv").read_bytes(), table)

    def test_archive_checks_accept_snapshots_without_a_media_table(self):
        from import_groups import validate_content
        folder = self.root / "snapshot"
        (folder / "media").mkdir(parents=True)
        (folder / "text.md").write_bytes(TEXT)
        (folder / "visual.svg").write_bytes(SVG)
        (folder / "media/detail.svg").write_bytes(SVG)
        files = ["text.md", "visual.svg", "media/detail.svg"]
        validate_content(folder, files)
        with self.assertRaisesRegex(ValueError, "media.csv: required"):
            validate_content(folder, files, require_table=True)

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
        self.assertEqual(validate_references(valid, {"media/detail.svg"}), [])
        self.assertEqual(validate_references("[link](media/missing.svg)", {"media/detail.svg"}),
                         ["text.md: media/missing.svg is not in this group yet"])
        for target in ("../other/visual.svg", "%2e%2e/other/visual.svg", "/static/main.js", "//other.test/photo.jpg", "javascript:alert"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_references(f"[link]({target})", {"media/detail.svg"})

    def test_typed_annotation_references_share_the_renderer_parser(self):
        validate_references("[[detail|media/detail.svg|style=italic;color=crimson]] "
                            "[[source|https://example.org|type=bibliography]] "
                            "[[quote|Quoted words|type=quote;style=italic;color=crimson]]",
                            {"media/detail.svg"})
        for target in ("../beta/visual.svg", "/static/main.js"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_references(f"[[detail|{target}|type=bibliography;style=italic]]",
                                    {"media/detail.svg"})

    def prepare_build(self, groups):
        (self.root / "groups.json").write_text(json.dumps({"groups": [vars(g) for g in groups]}))
        (self.root / "index.html").write_text("trusted site")
        (self.root / "static").mkdir()
        assets = Path(__file__).resolve().parent.parent
        shutil.copytree(assets / "editor", self.root / "editor")
        shutil.copytree(assets / "templates", self.root / "templates")
        output = self.root / "_site"
        output.mkdir()
        (output / "index.html").write_text("previous publication")
        return output

    def first_archive(self, groups):
        """Import into a new archive and build its site, as a first run does."""
        output = self.prepare_build(groups)
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            report = prepare_archive(groups, empty_archive(self.root / "empty-archive"), self.root / "archive",
                                     client_factory=self.api.client)
        return output, report

    def test_first_archive_imports_two_groups_and_builds_them(self):
        self.api.add("course/alpha")
        self.api.add("course/beta")
        output, report = self.first_archive([self.group(), self.group("beta")])
        self.assertEqual(json.loads((output / "entries.json").read_text()), ["alpha", "beta"])
        self.assertEqual((output / "alpha/media/detail.svg").read_bytes(), SVG)
        self.assertEqual((output / "index.html").read_text(), "trusted site")
        self.assertFalse((self.root / "alpha").exists())
        self.assertEqual([g["status"] for g in report["groups"]], ["imported", "imported"])

    def test_first_archive_skips_invalid_group_after_fetching_other_groups(self):
        self.api.add("course/beta")
        output, report = self.first_archive([self.group(), self.group("beta")])
        self.assertEqual(json.loads((output / "entries.json").read_text()), ["beta"])
        self.assertEqual([g["status"] for g in report["groups"]], ["failed", "imported"])
        self.assertEqual([g["publication_status"] for g in report["groups"]], ["skipped", "updated"])

    def test_local_path_collision_is_rejected_by_the_offline_build(self):
        output = self.prepare_build([self.group()])
        (self.root / "alpha").mkdir()
        (self.root / "alpha/text.md").write_text("Local entry")
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            with self.assertRaisesRegex(ValueError, "collides"):
                build_site.main()
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


    def test_raw_transport_never_forwards_api_credentials(self):
        self.client.headers["Authorization"] = "Bearer fixture-secret"
        self.client.opener.open.return_value = Response(b'bytes')
        self.assertEqual(self.client.raw_file("course/group", "a" * 40, "media/test.png", 5), b'bytes')
        request = self.client.opener.open.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).hostname, "raw.githubusercontent.com")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(self.client.metrics["rest_requests"], 0)
        self.assertEqual(self.client.metrics["raw_requests"], 1)
        self.assertEqual(self.client.metrics["raw_bytes"], 5)

    def test_api_headroom_defers_metadata_without_consuming_last_quota(self):
        self.client.api_remaining = 200
        with self.assertRaises(ImportFailure) as caught:
            self.client.json("/repos/course/group")
        self.assertEqual(caught.exception.code, "rate_limited")
        self.client.opener.open.assert_not_called()

    def test_full_cohort_metadata_budget_is_independent_of_file_count(self):
        api = FixtureAPI()
        groups = [Group(f"group-{i}", f"course/group-{i}", "main", "live") for i in range(15)]
        for group in groups:
            # 97 media + text.md + visual.svg + media.csv fill the 100-file limit.
            api.add(group.repository, {"text.md": b"Fixture", "visual.svg": SVG,
                                       **{f"media/{i}.png": b"image" for i in range(97)}})
        requests = 0
        with tempfile.TemporaryDirectory() as temporary:
            for cycle in range(6):
                results = import_groups(groups, Path(temporary) / str(cycle), api.client)
                self.assertTrue(all(result["status"] == "imported" for result in results))
                requests += sum(result["transport"]["rest_requests"] for result in results)
                self.assertEqual(sum(result["transport"]["raw_requests"] for result in results), 1500)
        self.assertEqual(requests, 360)


if __name__ == "__main__":
    unittest.main()
