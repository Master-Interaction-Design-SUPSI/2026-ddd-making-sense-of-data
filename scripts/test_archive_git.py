import hashlib
import subprocess
import tempfile
import unittest
import shutil
from pathlib import Path

from archive_git import (ARCHIVE_REF, MAIN_REF, accepted_main, accepted_authority, bounded_process, create_commit,
                         git, publish_commit, remote_heads, fetch_commit, snapshot_from_git)
from archive_state import encode_archive, seed_v1
from group_registry import Group
from publication_state import RecoveryError, encode_manifest
from test_import_groups import SVG


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
        manifest = encode_manifest({'alpha': {'repository': 'course/alpha', 'branch': 'main', 'commit': 'a' * 40,
                                             'files': {name: {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                                                       for name, content in data.items()}}})
        groups = seed_v1(manifest, [Group('alpha', 'course/alpha', 'main', True)], lambda slug, name: data[name])
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))
        (self.snapshot / 'alpha').mkdir()
        for name, content in data.items():
            (self.snapshot / 'alpha' / name).write_bytes(content)

    def accept_seed(self):
        commit = create_commit(self.writer, self.snapshot, None)
        publish_commit(self.writer, commit, self.main, None)
        return commit

    def change(self, text):
        from archive_state import blob_sha, decode_archive
        groups = decode_archive((self.snapshot / 'publication.json').read_bytes())
        groups['alpha']['snapshot']['files']['text.md'] = {
            'size': len(text), 'sha256': hashlib.sha256(text).hexdigest(), 'blob_sha': blob_sha(text)}
        (self.snapshot / 'alpha/text.md').write_bytes(text)
        (self.snapshot / 'publication.json').write_bytes(encode_archive(groups))

    def test_orphan_seed_then_normal_fast_forward_and_no_change(self):
        seed = self.accept_seed()
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', seed).decode().split(), [seed])
        self.assertEqual(create_commit(self.writer, self.snapshot, seed), seed)
        self.assertEqual(publish_commit(self.writer, seed, self.main, seed), seed)
        self.change(b'Second accepted version')
        second = create_commit(self.writer, self.snapshot, seed)
        publish_commit(self.writer, second, self.main, seed)
        self.assertEqual(remote_heads(self.writer), {MAIN_REF: self.main, ARCHIVE_REF: second})
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', second).decode().split(), [second, seed])

    def test_two_writers_cannot_replace_each_other_or_existing_seed(self):
        seed = self.accept_seed()
        self.change(b'Writer one')
        one = create_commit(self.writer, self.snapshot, seed)
        self.change(b'Writer two')
        two = create_commit(self.writer, self.snapshot, seed)
        publish_commit(self.writer, one, self.main, seed)
        with self.assertRaises(RecoveryError): publish_commit(self.writer, two, self.main, seed)
        with self.assertRaises(RecoveryError): publish_commit(self.writer, seed, self.main, None)
        self.assertEqual(remote_heads(self.writer)[ARCHIVE_REF], one)

    def test_main_change_and_wrong_parent_stop_mutation(self):
        seed = self.accept_seed()
        self.change(b'New content')
        candidate = create_commit(self.writer, self.snapshot, seed)
        with self.assertRaises(RecoveryError): publish_commit(self.writer, candidate, self.main, None)
        tree = git(self.writer, 'rev-parse', self.main + '^{tree}').decode().strip()
        newer_main = git(self.writer, 'commit-tree', tree, '-p', self.main, input=b'new main\n').decode().strip()
        git(self.writer, 'push', 'origin', newer_main + ':' + MAIN_REF)
        with self.assertRaises(RecoveryError): publish_commit(self.writer, candidate, self.main, seed)
        self.assertEqual(remote_heads(self.writer)[ARCHIVE_REF], seed)

    def test_unlisted_or_corrupt_bytes_cannot_enter_commit(self):
        (self.snapshot / 'alpha/unlisted.py').write_text('must never execute')
        with self.assertRaises(RecoveryError): create_commit(self.writer, self.snapshot, None)
        (self.snapshot / 'alpha/unlisted.py').unlink()
        (self.snapshot / 'alpha/text.md').write_bytes(b'wrong')
        with self.assertRaises(RecoveryError): create_commit(self.writer, self.snapshot, None)

    def test_pinned_shallow_transfer_reconstructs_without_sources_or_artifact(self):
        seed = self.accept_seed()
        shutil.rmtree(self.snapshot)
        reader = self.root / 'reader.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(reader)], check=True)
        git(reader, 'remote', 'add', 'origin', str(self.remote))
        self.assertGreater(fetch_commit(reader, seed), 0)
        restored = snapshot_from_git(reader, seed, self.root / 'restored')
        self.assertEqual(restored.commit, seed)
        self.assertEqual(restored.read_file('alpha', 'text.md'), b'Accepted content')
        self.assertEqual(git(reader, 'rev-list', '--count', seed).decode().strip(), '1')

    def test_acceptance_code_pin_survives_lost_run_artifacts_and_transfer_is_bounded(self):
        commit = create_commit(self.writer, self.snapshot, None, main_commit=self.main)
        self.assertEqual(accepted_main(self.writer, commit), self.main)
        with self.assertRaises(RecoveryError): accepted_main(self.writer, self.main)
        tar = self.root / 'bounded.tar'
        with tar.open('wb') as sink, self.assertRaises(RecoveryError):
            bounded_process(self.writer, ['archive', '--format=tar', commit], tar, stdout=sink, limit=1)
        self.assertNotIn(ARCHIVE_REF, remote_heads(self.writer))

    def test_authority_transition_is_durable_even_when_content_tree_is_unchanged(self):
        seed = create_commit(self.writer, self.snapshot, None, authority='v1-seed')
        self.assertEqual(accepted_authority(self.writer, seed), 'v1-seed')
        self.assertEqual(create_commit(self.writer, self.snapshot, seed, authority='v1-seed'), seed)
        cutover = create_commit(self.writer, self.snapshot, seed, authority='v2')
        self.assertNotEqual(cutover, seed)
        self.assertEqual(git(self.writer, 'rev-parse', seed + '^{tree}'),
                         git(self.writer, 'rev-parse', cutover + '^{tree}'))
        self.assertEqual(accepted_authority(self.writer, cutover), 'v2')
        self.assertEqual(git(self.writer, 'rev-list', '--parents', '-n', '1', cutover).decode().split(),
                         [cutover, seed])
        with self.assertRaisesRegex(RecoveryError, 'cannot replace V2'):
            create_commit(self.writer, self.snapshot, cutover, authority='v1-seed')
        with self.assertRaisesRegex(RecoveryError, 'authority marker'):
            accepted_authority(self.writer, self.main)


if __name__ == '__main__':
    unittest.main()
