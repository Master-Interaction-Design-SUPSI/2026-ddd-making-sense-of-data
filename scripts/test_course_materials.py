import shutil
import tempfile
import unittest
from pathlib import Path

from sync_course_materials import ROOT, START, END, sync


class CourseMaterialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / 'templates', self.root / 'templates')
        (self.root / 'README.md').write_text(f'Course introduction\n{START}\nstale\n{END}\nCourse layout\n')
        sync(self.root, write=True)

    def test_canonical_edits_fail_check_until_regenerated_and_preserve_root_sections(self):
        guide = self.root / 'templates/group/README.md'
        guide.write_text(guide.read_text() + '\nNew canonical instructions.\n')
        media = self.root / 'templates/group/media/example-tone.mp3'
        media.write_bytes(b'updated demonstration audio')
        before = (self.root / 'README.md').read_bytes()
        self.assertEqual(set(sync(self.root)), {Path('README.md')})
        self.assertEqual((self.root / 'README.md').read_bytes(), before)
        sync(self.root, write=True)
        readme = (self.root / 'README.md').read_text()
        self.assertTrue(readme.startswith('Course introduction\n'))
        self.assertTrue(readme.endswith('\nCourse layout\n'))
        self.assertIn('New canonical instructions.', readme)
        self.assertEqual(sync(self.root), [])

    def test_sync_does_not_create_a_standalone_example(self):
        sync(self.root, write=True)
        self.assertFalse((self.root / 'example').exists())

    def test_missing_markers_do_not_overwrite_root_readme(self):
        path = self.root / 'README.md'
        path.write_text('Unmarked hand-written guide')
        with self.assertRaisesRegex(ValueError, 'generated guide block'):
            sync(self.root, write=True)
        self.assertEqual(path.read_text(), 'Unmarked hand-written guide')
