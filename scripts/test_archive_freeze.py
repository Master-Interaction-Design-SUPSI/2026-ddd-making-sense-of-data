import copy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_freeze
import archive_git
import archive_release
import build_site
from archive_state import ArchiveSnapshot, encode_archive
from publication_state import RecoveryError
import test_archive_release as release_fixtures


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.base = release_fixtures.ReleasePairTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.archive = self.base.archive
        groups = copy.deepcopy(self.archive.groups)
        groups['alpha']['state'] = 'frozen'
        groups['beta']['state'] = 'hidden'
        (self.archive.folder / 'publication.json').write_bytes(encode_archive(groups))
        self.archive = ArchiveSnapshot(self.archive.folder, self.archive.commit)
        self.repository = self.base.fixture.workspace / 'freeze.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(self.repository)], check=True)
        commit = archive_git.create_commit(self.repository, self.archive.folder, None,
                                            main_commit='a' * 40)
        self.archive = ArchiveSnapshot(self.archive.folder, commit)
        self.release = archive_release.make_release('a' * 40, 'b' * 40, self.archive, '50', '1')

    def record(self):
        return archive_freeze.prepare_record(self.release, self.archive, 'a' * 40, 'b' * 40)

    def test_freeze_keeps_hidden_groups_hidden_and_rebuilds_every_byte_offline(self):
        record = self.record()
        hidden_bytes = self.archive.read_file('beta', 'text.md')
        expected = self.base.bytes()
        shutil.rmtree(build_site.OUTPUT)
        self.base.fixture.api.routes.clear()
        with patch.object(build_site, 'PublishedSite', side_effect=AssertionError('no Pages reads')), \
                patch.object(build_site, 'import_groups', side_effect=AssertionError('no source reads')):
            archive_freeze.reproduce(record, self.archive, 'a' * 40, 'b' * 40)
        self.assertEqual(self.base.bytes(), expected)
        self.assertEqual(set(self.archive.served()), {'alpha'})
        self.assertFalse((build_site.OUTPUT / 'beta').exists())
        self.assertEqual(self.archive.read_file('beta', 'text.md'), hidden_bytes)

    def test_annotated_tag_binds_complete_pair_and_cannot_be_moved(self):
        record = self.record()
        tag = archive_freeze.create_local_tag(self.repository, record, 'a' * 40, 'b' * 40)
        self.assertEqual(archive_freeze.verify_local_tag(self.repository, 'a' * 40, 'b' * 40), record)
        self.assertEqual(archive_git.git(self.repository, 'rev-parse', 'refs/tags/final-2026').decode().strip(), tag)
        with self.assertRaises(RecoveryError):
            archive_freeze.create_local_tag(self.repository, record, 'a' * 40, 'b' * 40)
        self.assertEqual(archive_freeze.verify_local_tag(self.repository, 'a' * 40, 'b' * 40), record)

    def test_live_group_wrong_code_and_other_archive_are_rejected(self):
        groups = copy.deepcopy(self.archive.groups)
        groups['alpha']['state'] = 'live'
        (self.archive.folder / 'publication.json').write_bytes(encode_archive(groups))
        live = ArchiveSnapshot(self.archive.folder, self.archive.commit)
        with self.assertRaisesRegex(RecoveryError, 'visible groups'):
            archive_freeze.prepare_record(self.release, live, 'a' * 40, 'b' * 40)
        (self.archive.folder / 'publication.json').write_bytes(self.archive.payload)
        with self.assertRaisesRegex(RecoveryError, 'exact recorded'):
            archive_freeze.prepare_record(self.release, self.archive, 'f' * 40, 'b' * 40)
        wrong = copy.deepcopy(self.release)
        wrong['archive_manifest_sha256'] = 'f' * 64
        with self.assertRaisesRegex(RecoveryError, 'exact recorded'):
            archive_freeze.create_local_tag(self.repository, {'version': 1, 'tag': 'final-2026', 'release': wrong},
                                            'a' * 40, 'b' * 40)

    def test_lightweight_and_mismatched_annotation_targets_are_rejected(self):
        archive_git.git(self.repository, 'update-ref', 'refs/tags/final-2026', self.archive.commit)
        with self.assertRaisesRegex(RecoveryError, 'annotated'):
            archive_freeze.verify_local_tag(self.repository, 'a' * 40, 'b' * 40)
        archive_git.git(self.repository, 'update-ref', '-d', 'refs/tags/final-2026')
        archive_freeze.create_local_tag(self.repository, self.record(), 'a' * 40, 'b' * 40)
        original = archive_git.git(self.repository, 'cat-file', '-p', 'refs/tags/final-2026')
        changed = original.replace(('"archive_commit": "' + self.archive.commit).encode(),
                                   ('"archive_commit": "' + 'f' * 40).encode())
        tag = archive_git.git(self.repository, 'mktag', input=changed).decode().strip()
        archive_git.git(self.repository, 'update-ref', 'refs/tags/final-2026', tag)
        with self.assertRaisesRegex(RecoveryError, 'target differs'):
            archive_freeze.verify_local_tag(self.repository, 'a' * 40, 'b' * 40)

    def test_verification_reproduces_the_pair_and_rejects_tampered_annotation_fields(self):
        record = self.record()
        archive_freeze.create_local_tag(self.repository, record, 'a' * 40, 'b' * 40)
        original = archive_git.git(self.repository, 'cat-file', '-p', 'refs/tags/final-2026')
        for field in ('main_commit', 'main_tree', 'archive_manifest_sha256',
                      'served_manifest_sha256', 'site_sha256'):
            with self.subTest(field=field):
                changed = copy.deepcopy(record)
                changed['release'][field] = 'f' * len(changed['release'][field])
                header = original.split(b'\n\n', 1)[0]
                from publish_site import encode
                tag = archive_git.git(self.repository, 'mktag', input=header + b'\n\n' + encode(changed)).decode().strip()
                archive_git.git(self.repository, 'update-ref', 'refs/tags/final-2026', tag)
                with self.assertRaises(RecoveryError):
                    archive_freeze.verify_local_tag(self.repository, 'a' * 40, 'b' * 40)


if __name__ == '__main__':
    unittest.main()
