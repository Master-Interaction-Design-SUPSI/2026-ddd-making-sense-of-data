import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_site
from group_registry import Group


class EditorBuildTests(unittest.TestCase):
    def test_generated_preview_uses_production_shell_with_only_deliberate_substitutions(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            registry = [Group('city-water', 'course/city-water', 'main', 'live', ('media/12.mov',)),
                        Group('pilot', 'course/pilot', 'main', 'hidden')]
            with patch.object(build_site, 'load_registry', return_value=registry):
                build_site.write_editor(destination)
            production = (build_site.ROOT / 'index.html').read_text()
            preview = (destination / 'editor/preview.html').read_text()
            expected = production.replace('href="static/', 'href="../static/').replace('src="static/', 'src="../static/')
            expected = expected.replace('<script src="../static/bootstrap.js"></script>',
                                        '<script src="protocol.js"></script>\n    <script src="preview.js"></script>')
            self.assertEqual(preview, expected)
            groups = json.loads((destination / 'editor/groups.json').read_text())['groups']
            self.assertEqual(groups, [
                {'slug': 'city-water', 'repository': 'course/city-water', 'branch': 'main', 'exclude': ['media/12.mov'],
                 'state': 'live', 'editable': True, 'contract_version': 2},
                {'slug': 'pilot', 'repository': 'course/pilot', 'branch': 'main', 'exclude': [],
                 'state': 'hidden', 'editable': False, 'contract_version': 2}])
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
