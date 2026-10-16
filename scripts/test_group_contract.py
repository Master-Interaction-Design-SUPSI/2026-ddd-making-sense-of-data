import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_site
from content_contract import (
    MAX_MEDIA_BYTES, MAX_SVG_BYTES, MAX_TEXT_BYTES, select_content_files, validate_visual,
)
from group_registry import load_registry
from import_groups import validate_content


ROOT = Path(__file__).resolve().parent.parent


class GroupContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = self.root / "groups.json"

    def load(self, groups):
        self.registry.write_text(json.dumps({"groups": groups}), encoding="utf-8")
        return load_registry(self.registry)

    def group(self, **changes):
        return {"slug": "city-water", "repository": "course/city-water", "state": "live", **changes}

    def test_empty_registry_and_branch_default(self):
        self.assertEqual(self.load([]), [])
        group = self.load([self.group()])[0]
        self.assertEqual(group.branch, "main")
        self.assertEqual(group.state, "live")

    def test_state_is_required_and_explicit(self):
        for state in ('live', 'frozen', 'hidden'):
            self.assertEqual(self.load([self.group(state=state)])[0].state, state)
        for state in ('unknown', True, None, ['live']):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.load([self.group(state=state)])
        entry = self.group()
        entry.pop('state')
        with self.assertRaisesRegex(ValueError, 'state'):
            self.load([entry])
        with self.assertRaises(ValueError):
            self.load([self.group(enabled=True)])

    def test_optional_media_exclusions(self):
        group = self.load([self.group(exclude=["media/12.mov"])])[0]
        self.assertEqual(group.exclude, ("media/12.mov",))
        for exclude in ("media/12.mov", ["text.md"], ["visual.svg"],
                        ["../movie.mov"], ["media/movie.mov", "media/movie.mov"],
                        ["other/movie.mov"], ["media/code.js"], [None]):
            with self.subTest(exclude=exclude), self.assertRaises(ValueError):
                self.load([self.group(exclude=exclude)])

    def test_every_group_needs_the_main_visual_and_only_course_team_folders_may_omit_it(self):
        with self.assertRaises(ValueError):
            self.load([self.group(require_visual=False)])
        with self.assertRaisesRegex(ValueError, "visual.svg"):
            select_content_files({"text.md": 1})
        self.assertEqual(select_content_files({"text.md": 1}, required=("text.md",)), ["text.md"])
        with self.assertRaises(ValueError):
            select_content_files({}, required=("text.md",))

    def test_repository_change_preserves_slug(self):
        before = self.load([self.group()])[0]
        after = self.load([self.group(repository="new-owner/renamed", state="hidden", branch="draft/content")])[0]
        self.assertEqual(before.slug, after.slug)
        self.assertEqual(after.state, "hidden")
        self.assertEqual(after.branch, "draft/content")

    def test_personal_repository_owners_need_not_share_an_organization(self):
        groups = self.load([
            self.group(repository="alice-student/project"),
            self.group(slug="other", repository="bob-student/project"),
        ])
        self.assertEqual([group.repository for group in groups], [
            "alice-student/project", "bob-student/project",
        ])

    def test_rejects_reserved_unsafe_and_duplicate_slugs(self):
        for slug in ("example", "static", "scripts", "templates", "_site", "index", "entries", "groups", "../escape", "MixedCase", "two--hyphens", "a" * 64):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                self.load([self.group(slug=slug)])
        with self.assertRaisesRegex(ValueError, "duplicate slug"):
            self.load([self.group(), self.group(repository="course/other")])

    def test_rejects_repository_urls_and_case_insensitive_duplicates(self):
        for repository in ("https://github.com/course/group", "course/group.git", "course/group/extra", "../group", None):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                self.load([self.group(repository=repository)])
        with self.assertRaisesRegex(ValueError, "duplicate repository"):
            self.load([self.group(), self.group(slug="other", repository="Course/City-Water")])

    def test_rejects_mistyped_fields_including_hidden_entries(self):
        for entry in (
            self.group(state="false"), self.group(brnach="draft"),
            {"slug": "city-water", "repository": "course/city-water"},
            self.group(branch="draft/../main", state="hidden"),
            self.group(branch="draft//main"), self.group(branch="draft.lock"),
            self.group(branch="draft/.hidden"), self.group(branch="main;echo"),
            self.group(branch=None),
        ):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.load([entry])

    def test_rejects_malformed_registry_structure(self):
        for registry in ([], {}, {"groups": {}}, {"groups": [], "unexpected": True}, {"groups": [None]}):
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                load_registry(self.registry)

    def test_only_supported_content_is_selected(self):
        files = {
            "text.md": 100, "visual.svg": 500, "photo.jpg": 80,
            "media/detail.svg": 50, "media/audio/clip.mp3": 100,
            "media/clip.mp4": 200, "README.md": 100,
            ".github/workflows/run.yml": 100, "index.html": 100,
            "script.js": 100, "media/script.js": 100, "design/source.ai": 100,
            "other/photo.jpg": 100,
        }
        self.assertEqual(select_content_files(files), [
            "media/audio/clip.mp3", "media/clip.mp4", "media/detail.svg",
            "photo.jpg", "text.md", "visual.svg",
        ])

    def test_rejects_bad_paths_and_conflicting_main_visuals(self):
        for name in ("../photo.jpg", "/photo.jpg", "media/../photo.jpg", "media//photo.jpg", "media\\photo.jpg", "media/Photo.JPG", "media/photo name.jpg", "media/.hidden.jpg", "visual.png", "visual.SVG", "bad\nname.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                select_content_files({"text.md": 1, "visual.svg": 1, name: 1})

    def test_requires_both_files_and_enforces_individual_limits(self):
        for files in ({}, {"text.md": 10}, {"visual.svg": 10}):
            with self.subTest(files=files), self.assertRaisesRegex(ValueError, "requires"):
                select_content_files(files)
        for name, limit in (("text.md", MAX_TEXT_BYTES), ("visual.svg", MAX_SVG_BYTES), ("media/clip.mp4", MAX_MEDIA_BYTES)):
            files = {"text.md": 1, "visual.svg": 1, name: limit}
            self.assertIn(name, select_content_files(files))
            files[name] += 1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "file limit"):
                select_content_files(files)

    def test_enforces_group_limits_and_valid_sizes(self):
        files = {"text.md": 1, "visual.svg": 1}
        files.update({f"media/{number}.png": 1 for number in range(98)})
        self.assertEqual(len(select_content_files(files)), 100)
        with self.assertRaisesRegex(ValueError, "100 files"):
            select_content_files({**files, "media/extra.png": 1})
        with self.assertRaisesRegex(ValueError, "50 MiB"):
            select_content_files({"text.md": 1, "visual.svg": 1, **{f"{n}.mp4": MAX_MEDIA_BYTES for n in range(5)}})
        for size in (-1, True, "10"):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "invalid file size"):
                select_content_files({"text.md": size, "visual.svg": 1})

    def test_template_matches_contract_and_is_not_discovered_as_an_entry(self):
        template = ROOT / "templates" / "group"
        files = {path.relative_to(template).as_posix(): path.stat().st_size for path in template.rglob("*") if path.is_file()}
        self.assertEqual(select_content_files(files), ["media.csv", "media/example-detail.svg", "media/example-motion.mp4", "media/example-tall.svg", "media/example-tone.mp3", "text.md", "visual.svg"])
        validate_visual(template / "visual.svg")
        text = (template / "text.md").read_text(encoding="utf-8")
        self.assertIn("invented", text)
        self.assertIn("[[wide detail]]", text)
        # The template passes the same admission check as a student repository.
        self.assertEqual(validate_content(template, select_content_files(files)), [])
        self.assertNotIn("templates", build_site.find_entries())
        load_registry(ROOT / "groups.json")

    def test_reserved_template_directory_cannot_be_published_as_a_group(self):
        template = self.root / "templates"
        template.mkdir()
        (template / "text.md").write_text("not a student entry")
        (template / "visual.svg").write_text("not even a valid SVG")
        with patch.object(build_site, "ROOT", self.root):
            self.assertEqual(build_site.find_entries(), [])

    def test_invalid_registry_preserves_previous_build(self):
        self.registry.write_text('{"groups": "wrong"}')
        output = self.root / "_site"
        output.mkdir()
        page = output / "index.html"
        page.write_text("previous publication")
        with patch.object(build_site, "ROOT", self.root), patch.object(build_site, "OUTPUT", output):
            with self.assertRaises(ValueError):
                build_site.main()
        self.assertEqual(page.read_text(), "previous publication")


if __name__ == "__main__":
    unittest.main()
