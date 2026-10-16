import subprocess
import tempfile
import unittest
import shutil
from pathlib import Path

from archive_git import (ARCHIVE_REF, MAIN_REF, bounded_process, create_commit,
                         git, publish_commit, remote_heads, fetch_commit, snapshot_from_git)
from archive_state import decode_archive, empty_archive, encode_archive, file_record
from release import PublicationError
from test_fixtures import SVG, archived_group


class ArchiveGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / 'remote.git'
        self.writer = self.root / 'writer.git'
        for path in (self.remote, self.writer):
            subprocess.run(['git', 'init', '--bare', '--quiet', str(path)], check=True)
        git(self.writer, 'remote', 'add', 'origin', str(self.remote))
        tree = git(self.writer, 'mktree', input=b'').decode().strip()
        self.main = git(self.writer, 'commit-tree', tree, input=b'trusted main\n').decode().strip()
        git(self.writer, 'push', 'origin', self.main + ':' + MAIN_REF)
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        data = {'text.md': b'Accepted content', 'visual.svg': SVG}
        (self.snapshot / 'publication.json').write_bytes(encode_archive({'alpha': archived_group(data)}))
        (self.snapshot / 'alpha').mkdir()
        for name, content in data.items():
            (self.snapshot / 'alpha' / name).write_bytes(content)

    def accept_first(self):
        commit = create_commit(self.writer, self.snapshot, None, main_commit=self.main)
        publish_commit(self.writer, commit, self.main, None)
        return commit

    def change(self, text):
        groups = decode_archive((self.snapshot / 'publication.json').read_bytes())
        groups['alpha']['snapshot']['files']['text.md'] = file_record(text)
        (self.snapshot / 'alpha/text.md').write_bytes(text)
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))

    def test_first_run_root_then_normal_fast_forward_and_no_change(self):
        first = self.accept_first()
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', first).decode().split(), [first])
        self.assertEqual(create_commit(self.writer, self.snapshot, first, main_commit=self.main), first)
        self.assertEqual(publish_commit(self.writer, first, self.main, first), first)
        self.change(b'Second accepted version')
        second = create_commit(self.writer, self.snapshot, first, main_commit=self.main)
        publish_commit(self.writer, second, self.main, first)
        self.assertEqual(remote_heads(self.writer), {MAIN_REF: self.main, ARCHIVE_REF: second})
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', second).decode().split(), [second, first])

    def test_two_writers_cannot_replace_each_other_or_the_first_archive(self):
        first = self.accept_first()
        self.change(b'Writer one')
        one = create_commit(self.writer, self.snapshot, first, main_commit=self.main)
        self.change(b'Writer two')
        two = create_commit(self.writer, self.snapshot, first, main_commit=self.main)
        publish_commit(self.writer, one, self.main, first)
        with self.assertRaises(PublicationError): publish_commit(self.writer, two, self.main, first)
        with self.assertRaises(PublicationError): publish_commit(self.writer, first, self.main, None)
        self.assertEqual(remote_heads(self.writer)[ARCHIVE_REF], one)

    def test_main_change_and_wrong_parent_stop_mutation(self):
        first = self.accept_first()
        self.change(b'New content')
        candidate = create_commit(self.writer, self.snapshot, first, main_commit=self.main)
        with self.assertRaises(PublicationError): publish_commit(self.writer, candidate, self.main, None)
        tree = git(self.writer, 'rev-parse', self.main + '^{tree}').decode().strip()
        newer_main = git(self.writer, 'commit-tree', tree, '-p', self.main, input=b'new main\n').decode().strip()
        git(self.writer, 'push', 'origin', newer_main + ':' + MAIN_REF)
        with self.assertRaises(PublicationError): publish_commit(self.writer, candidate, self.main, first)
        self.assertEqual(remote_heads(self.writer)[ARCHIVE_REF], first)

    def test_unlisted_or_corrupt_bytes_cannot_enter_commit(self):
        (self.snapshot / 'alpha/unlisted.py').write_text('must never execute')
        with self.assertRaises(PublicationError): create_commit(self.writer, self.snapshot, None, main_commit=self.main)
        (self.snapshot / 'alpha/unlisted.py').unlink()
        (self.snapshot / 'alpha/text.md').write_bytes(b'wrong')
        with self.assertRaises(PublicationError): create_commit(self.writer, self.snapshot, None, main_commit=self.main)

    def test_pinned_shallow_transfer_reconstructs_without_sources_or_artifact(self):
        first = self.accept_first()
        shutil.rmtree(self.snapshot)
        reader = self.root / 'reader.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(reader)], check=True)
        git(reader, 'remote', 'add', 'origin', str(self.remote))
        self.assertGreater(fetch_commit(reader, first), 0)
        restored = snapshot_from_git(reader, first, self.root / 'restored')
        self.assertEqual(restored.commit, first)
        self.assertEqual(restored.read_file('alpha', 'text.md'), b'Accepted content')
        self.assertEqual(git(reader, 'rev-list', '--count', first).decode().strip(), '1')

    def test_first_archive_records_its_code_and_an_empty_registry_still_gets_a_root(self):
        commit = self.accept_first()
        self.assertIn('Main-Commit: ' + self.main, git(self.writer, 'show', '-s', '--format=%B', commit).decode())
        self.assertNotIn('Archive-Authority', git(self.writer, 'show', '-s', '--format=%B', commit).decode())
        empty = empty_archive(self.root / 'empty')
        root = create_commit(self.writer, empty.folder, None, main_commit=self.main)
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', root).decode().split(), [root])
        self.assertEqual(git(self.writer, 'ls-tree', '--name-only', root).decode().split(), ['publication.json'])

    def test_transfer_is_bounded(self):
        commit = create_commit(self.writer, self.snapshot, None, main_commit=self.main)
        tar = self.root / 'bounded.tar'
        with tar.open('wb') as sink, self.assertRaises(PublicationError):
            bounded_process(self.writer, ['archive', '--format=tar', commit], tar, stdout=sink, limit=1)
        self.assertNotIn(ARCHIVE_REF, remote_heads(self.writer))

if __name__ == '__main__':
    unittest.main()
