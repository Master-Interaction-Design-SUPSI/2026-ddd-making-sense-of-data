import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_site
from content_contract import MAX_MEDIA_BYTES, MAX_TOTAL_BYTES, select_content_files
from publication_state import decode_manifest


class EditorBuildTests(unittest.TestCase):
    def test_generated_preview_uses_production_shell_with_only_deliberate_substitutions(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            build_site.write_editor(destination)
            production = (build_site.ROOT / 'index.html').read_text()
            preview = (destination / 'editor/preview.html').read_text()
            expected = production.replace('href="static/', 'href="../static/').replace('src="static/', 'src="../static/')
            expected = expected.replace('<script src="../static/bootstrap.js"></script>',
                                        '<script src="protocol.js"></script>\n    <script src="preview.js"></script>')
            self.assertIn('<a class="bar-link" href="editor/">', expected)
            expected = expected.replace('<a class="bar-link" href="editor/">', '<a class="bar-link">')
            self.assertEqual(preview, expected)
            groups = json.loads((destination / 'editor/groups.json').read_text())['groups']
            abstract = next(group for group in groups if group['slug'] == 'ddd-abstract')
            self.assertFalse(abstract['require_visual'])
            self.assertTrue(abstract['editable'])
            brief = next(group for group in groups if group['slug'] == 'materialities-of-ai')
            self.assertFalse(brief['editable'])
            self.assertEqual(abstract['exclude'], ['media/12.mov', 'media/4.mov'])
            template = json.loads((destination / 'editor/template.json').read_text())
            self.assertIn('media/example-tone.mp3', template['files'])
            for name, record in template['files'].items():
                self.assertEqual((destination / 'editor/template' / name).stat().st_size, record['size'])

    def test_site_budget_is_checked_before_replacing_previous_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / '_site'
            output.mkdir()
            (output / 'index.html').write_text('previous')
            with patch.object(build_site, 'OUTPUT', output), patch.object(build_site, 'MAX_SITE_BYTES', 1):
                with self.assertRaisesRegex(ValueError, 'site limit'):
                    build_site.write_site({})
            self.assertEqual((output / 'index.html').read_text(), 'previous')

    def test_legacy_recovery_keeps_snapshots_above_new_admission_cap_readable(self):
        files = {'text.md': 1, 'visual.svg': 1, **{f'media/{i}.mp4': MAX_MEDIA_BYTES for i in range(3)}}
        self.assertGreater(sum(files.values()), MAX_TOTAL_BYTES)
        with self.assertRaisesRegex(ValueError, '50 MiB'):
            select_content_files(files)
        manifest = {'version': 1, 'groups': {'group': {
            'repository': 'course/group', 'branch': 'main', 'commit': 'a' * 40,
            'files': {name: {'size': size, 'sha256': 'a' * 64} for name, size in files.items()},
        }}}
        self.assertIn('group', decode_manifest(json.dumps(manifest)))
