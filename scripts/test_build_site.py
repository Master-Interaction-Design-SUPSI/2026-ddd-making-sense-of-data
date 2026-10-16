import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import build_site


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" {attributes}/>'


class EntryValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "groups.json").write_text('{"groups": []}')
        self.root_patch = patch.object(build_site, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def entry(self, name="group", visual=None, filename="visual.svg", text=True):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        if text:
            (folder / "text.md").write_text("Our work")
        if visual is not None:
            (folder / filename).write_text(visual)
        return folder

    def test_accepts_svg_exports_and_orders_alphabetically(self):
        for name, attributes in (
            ("zebra", ''),
            ("alpha", 'width="1920px" height="1080px"'),
            ("example", 'width="1920.0" height="1080"'),
        ):
            self.entry(name, SVG.format(attributes=attributes))
        self.assertEqual(build_site.find_entries(), ["alpha", "example", "zebra"])

    def test_accepts_comma_separated_viewbox(self):
        self.entry(visual=SVG.format(attributes="").replace("0 0 1920 1080", "0,0,1920,1080"))
        self.assertEqual(build_site.find_entries(), ["group"])

    def test_rejects_wrong_format_and_duplicate_visuals(self):
        for filename in ("visual.png", "visual.jpg", "visual.avif", "visual.SVG"):
            for with_svg in (False, True):
                with self.subTest(filename=filename, with_svg=with_svg):
                    folder = self.entry(visual="raster", filename=filename)
                    if with_svg:
                        (folder / "visual.svg").write_text(SVG.format(attributes=""))
                    with self.assertRaisesRegex(ValueError, "exactly visual.svg"):
                        build_site.find_entries()
                    (folder / filename).unlink()
                    (folder / "visual.svg").unlink(missing_ok=True)

    def test_rejects_invalid_svg_and_wrong_canvas(self):
        for visual in (
            "a renamed PNG", "<svg", '<html/>',
            '<svg xmlns="http://www.w3.org/2000/svg"/>',
            SVG.format(attributes="").replace("1920 1080", "960 540"),
            SVG.format(attributes="").replace("0 0 1920", "1 0 1920"),
            SVG.format(attributes='width="960" height="540"'),
            SVG.format(attributes='width="100%"'),
        ):
            with self.subTest(visual=visual):
                self.entry(visual=visual)
                with self.assertRaises(ValueError):
                    build_site.find_entries()

    def test_course_team_folders_may_omit_the_visual_but_not_the_text(self):
        self.entry("text-only")
        self.entry("visual-only", SVG.format(attributes=""), text=False)
        folder = self.entry("complete", SVG.format(attributes=""))
        (folder / "photo.jpg").write_bytes(b"extra media")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(build_site.find_entries(), ["complete", "text-only"])
        self.assertEqual(len(caught), 1)

    def test_course_team_folders_get_the_same_content_checks_as_an_import(self):
        folder = self.entry("brief")
        for name, content, message in (
                ("text.md", "See ![x](../other/photo.png)", "brief: text.md: Reference must stay inside this group"),
                ("media/detail.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "brief: detail.svg"),
                ("media/Photo.png", "x", "brief: media/Photo.png")):
            with self.subTest(name=name):
                (folder / "text.md").write_text("Our work")
                target = folder / name
                target.parent.mkdir(exist_ok=True)
                target.write_text(content)
                with self.assertRaisesRegex(ValueError, message):
                    build_site.find_entries()
                if name != "text.md":
                    target.unlink()

    def test_course_team_folder_reference_that_does_not_resolve_is_a_warning(self):
        folder = self.entry("brief")
        (folder / "text.md").write_text("See [[ghost]] and ![x](media/later.png).")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(build_site.find_entries(), ["brief"])
        self.assertEqual([str(warning.message) for warning in caught], [
            "brief: text.md: [[ghost]] is not a name in media.csv",
            "brief: text.md: media/later.png is not in this group yet"])

    def test_validation_failure_preserves_previous_build(self):
        self.entry(visual="invalid")
        output = self.root / "_site"
        output.mkdir()
        previous = output / "index.html"
        previous.write_text("previous build")
        with patch.object(build_site, "OUTPUT", output), self.assertRaises(ValueError):
            build_site.main()
        self.assertEqual(previous.read_text(), "previous build")


if __name__ == "__main__":
    unittest.main()
