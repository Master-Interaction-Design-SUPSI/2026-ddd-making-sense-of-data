import hashlib
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publication_recovery
import test_publish_site as publish_fixtures
from archive_state import ArchiveSnapshot, blob_sha, decode_archive, encode_archive, seed_v1
from group_registry import load_registry
from publication_state import RecoveryError
from publication_recovery import (capture_v1, compare_v1_seed, stage_rollback,
                                  verify_capture)
from publish_site import RELEASE
from content_contract import MAX_SITE_BYTES


class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publish_fixtures.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bootstrap()
        self.workspace = self.fixture.fixture.workspace
        self.capture = self.workspace / 'v1-capture'
        self.release = json.loads((self.fixture.fixture.output / 'release.json').read_bytes())

    def capture_original(self):
        capture_v1(self.fixture.fixture.output, self.release, self.capture)
        return verify_capture(self.capture)

    def archive_snapshot(self):
        live = self.fixture.fixture.output
        manifest = (live / 'publication.json').read_bytes()
        groups = seed_v1(manifest, load_registry(self.fixture.fixture.root / 'groups.json'),
                         lambda slug, name: (live / slug / name).read_bytes())
        folder = self.workspace / 'seed'
        folder.mkdir()
        for slug, group in groups.items():
            if group['snapshot']:
                shutil.copytree(live / slug, folder / slug)
        (folder / 'publication.json').write_bytes(encode_archive(groups))
        return ArchiveSnapshot(folder, 'c' * 40)

    def test_capture_binds_original_v1_code_and_all_site_bytes(self):
        metadata = self.capture_original()
        self.assertEqual(metadata['source_commit'], self.release['source_commit'])
        self.assertEqual(metadata['source_tree'], self.release['source_tree'])
        self.assertEqual(metadata['site_sha256'], self.release['site_sha256'])
        self.assertEqual(metadata['manifest_sha256'], self.release['manifest_sha256'])
        self.assertEqual(verify_capture(self.capture), metadata)
        captured_files = {p.relative_to(self.capture / 'site').as_posix(): p.read_bytes()
                          for p in (self.capture / 'site').rglob('*') if p.is_file()}
        original_files = {p.relative_to(self.fixture.fixture.output).as_posix(): p.read_bytes()
                          for p in self.fixture.fixture.output.rglob('*') if p.is_file()}
        self.assertEqual(captured_files, original_files)
        with self.assertRaisesRegex(RecoveryError, 'must not already exist'):
            capture_v1(self.fixture.fixture.output, self.release, self.capture)

    def test_capture_remains_verifiable_after_original_artifact_is_gone(self):
        self.capture_original()
        shutil.rmtree(self.fixture.fixture.output)
        # The capture needs no Pages URL, source repository or transient build cache.
        self.fixture.fixture.api.routes.clear()
        self.assertEqual(verify_capture(self.capture)['release'], self.release)
        staged = self.workspace / 'rollback-after-source-loss'
        plan = stage_rollback(self.capture, staged, 'c' * 40)
        self.assertEqual(plan['source_commit'], self.release['source_commit'])
        self.assertTrue((staged / 'site/release.json').is_file())

    def test_capture_rejects_v2_release_changed_manifest_and_content(self):
        v2 = {'version': 2, 'run_id': '1', 'run_attempt': '1', 'main_commit': 'a' * 40,
              'main_tree': 'b' * 40, 'archive_commit': 'c' * 40,
              'archive_manifest_sha256': 'd' * 64, 'served_manifest_sha256': 'e' * 64,
              'site_sha256': 'f' * 64, 'probes': []}
        with self.assertRaisesRegex(RecoveryError, 'V2'):
            capture_v1(self.fixture.fixture.output, v2, self.capture)
        (self.fixture.fixture.output / 'alpha/text.md').write_bytes(b'tampered')
        with self.assertRaisesRegex(RecoveryError, 'content differs'):
            capture_v1(self.fixture.fixture.output, self.release, self.capture)

    def test_verify_rejects_tampering_extras_and_links(self):
        self.capture_original()
        text = self.capture / 'site/alpha/text.md'
        text.write_bytes(b'tampered')
        with self.assertRaises(RecoveryError):
            verify_capture(self.capture)
        # Restore one exact copy, then exercise unlisted bundle entries.
        shutil.rmtree(self.capture)
        self.capture_original()
        (self.capture / 'extra.txt').write_text('extra')
        with self.assertRaisesRegex(RecoveryError, 'extra root'):
            verify_capture(self.capture)
        (self.capture / 'extra.txt').unlink()
        link = self.capture / 'site' / 'unsafe-link'
        link.symlink_to(self.capture / 'site/index.html')
        with self.assertRaisesRegex(RecoveryError, 'symbolic link'):
            verify_capture(self.capture)

    def test_shadow_comparison_checks_inventory_and_every_included_byte(self):
        self.capture_original()
        archive = self.archive_snapshot()
        result = compare_v1_seed(self.capture, archive)
        self.assertTrue(result['matches'], result['differences'])
        self.assertEqual(result['published_files_compared'], sum(
            len(group['files']) for group in archive.served().values()))
        self.assertTrue(result['intentional_metadata_differences'])

        replacement = b'Archive-only but valid content'
        groups = decode_archive((archive.folder / 'publication.json').read_bytes())
        groups['alpha']['snapshot']['files']['text.md'] = {
            'size': len(replacement),
            'sha256': hashlib.sha256(replacement).hexdigest(),
            'blob_sha': blob_sha(replacement),
        }
        (archive.folder / 'alpha/text.md').write_bytes(replacement)
        (archive.folder / 'publication.json').write_bytes(encode_archive(groups))
        changed = ArchiveSnapshot(archive.folder, archive.commit)
        result = compare_v1_seed(self.capture, changed)
        self.assertFalse(result['matches'])
        self.assertTrue(any('published bytes differ' in item for item in result['differences']))

    def test_stage_records_v2_archive_head_and_does_not_mutate_local_archive_ref(self):
        self.capture_original()
        repository = self.workspace / 'archive.git'
        repository.mkdir()
        subprocess.run(['git', 'init', '-q', str(repository)], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Recovery test'], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'recovery@example.test'], check=True)
        (repository / 'publication.json').write_text('v2 archive')
        subprocess.run(['git', '-C', str(repository), 'add', 'publication.json'], check=True)
        subprocess.run(['git', '-C', str(repository), 'commit', '-q', '-m', 'V2 accepted archive'], check=True)
        subprocess.run(['git', '-C', str(repository), 'branch', '-M', 'student-work'], check=True)
        before = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'refs/heads/student-work'], text=True).strip()

        staged = self.workspace / 'rollback-stage'
        plan = stage_rollback(self.capture, staged, before)
        after = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'refs/heads/student-work'], text=True).strip()
        self.assertEqual(after, before)
        self.assertEqual(plan['archive_head_to_preserve'], before)
        self.assertFalse(plan['deployment_performed'])
        self.assertEqual(plan['source_commit'], self.release['source_commit'])
        staged_files = {p.relative_to(staged / 'site').as_posix(): p.read_bytes()
                        for p in (staged / 'site').rglob('*') if p.is_file()}
        capture_files = {p.relative_to(self.capture / 'site').as_posix(): p.read_bytes()
                         for p in (self.capture / 'site').rglob('*') if p.is_file()}
        self.assertEqual(staged_files, capture_files)
        self.assertTrue((staged / 'rollback-plan.json').is_file())

    def test_stage_requires_exact_archive_pin_and_new_destination(self):
        self.capture_original()
        with self.assertRaisesRegex(RecoveryError, 'exact current V2 archive commit'):
            stage_rollback(self.capture, self.workspace / 'bad-stage', 'student-work')
        destination = self.workspace / 'rollback-stage'
        destination.mkdir()
        with self.assertRaisesRegex(RecoveryError, 'must not already exist'):
            stage_rollback(self.capture, destination, 'c' * 40)

    def test_capture_enforces_total_byte_and_inventory_bounds_before_hashing(self):
        with patch.object(publication_recovery, 'MAX_SITE_BYTES', 1):
            with self.assertRaisesRegex(RecoveryError, 'byte limit'):
                capture_v1(self.fixture.fixture.output, self.release, self.capture)
        with patch.object(publication_recovery, 'MAX_RELEASE_BYTES', 1), \
                patch.object(publication_recovery.hashlib, 'sha256', side_effect=AssertionError('read release before bound')):
            with self.assertRaisesRegex(RecoveryError, 'release metadata.*byte limit'):
                capture_v1(self.fixture.fixture.output, self.release, self.capture)
        with patch.object(publication_recovery, 'MAX_MANIFEST_BYTES', 1), \
                patch.object(publication_recovery.hashlib, 'sha256', side_effect=AssertionError('read manifest before bound')):
            with self.assertRaisesRegex(RecoveryError, 'manifest.*byte limit'):
                capture_v1(self.fixture.fixture.output, self.release, self.capture)
        with patch.object(publication_recovery, 'MAX_INVENTORY_ENTRIES', 1), \
                patch.object(publication_recovery.hashlib, 'sha256', side_effect=AssertionError('hashed before bounds')):
            with self.assertRaisesRegex(RecoveryError, 'entry limit'):
                capture_v1(self.fixture.fixture.output, self.release, self.capture)

    def test_verify_bounds_capture_metadata_inventory(self):
        self.capture_original()
        metadata_path = self.capture / 'capture.json'
        metadata = json.loads(metadata_path.read_bytes())
        first = next(iter(metadata['inventory']))
        metadata['inventory'][first]['size'] = publication_recovery.MAX_SITE_BYTES + 1
        metadata_path.write_bytes(publication_recovery.encode(metadata))
        with self.assertRaisesRegex(RecoveryError, 'byte limit'):
            verify_capture(self.capture)

    def test_local_cli_capture_verify_and_invalid_expected_release(self):
        script = Path(publication_recovery.__file__)

        def run(*arguments):
            return subprocess.run([sys.executable, str(script), *map(str, arguments)],
                                  capture_output=True, text=True, check=False)

        result = run('capture-v1', '--site', self.fixture.fixture.output,
                     '--release', self.fixture.fixture.output / 'release.json', '--out', self.capture)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run('verify-capture', '--capture', self.capture,
                     '--expected-release', self.fixture.fixture.output / 'release.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = self.archive_snapshot()
        result = run('compare-seed', '--capture', self.capture,
                     '--archive-snapshot', archive.folder, '--archive-commit', archive.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['matches'])
        staged = self.workspace / 'cli-rollback-stage'
        result = run('stage-rollback', '--capture', self.capture, '--archive-commit', 'c' * 40,
                     '--out', staged, '--expected-release', self.fixture.fixture.output / 'release.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['deployment_performed'])

        changed_bytes = b'valid but different archive content'
        groups = decode_archive((archive.folder / 'publication.json').read_bytes())
        groups['alpha']['snapshot']['files']['text.md'] = {
            'size': len(changed_bytes),
            'sha256': hashlib.sha256(changed_bytes).hexdigest(),
            'blob_sha': blob_sha(changed_bytes),
        }
        (archive.folder / 'alpha/text.md').write_bytes(changed_bytes)
        (archive.folder / 'publication.json').write_bytes(encode_archive(groups))
        result = run('compare-seed', '--capture', self.capture,
                     '--archive-snapshot', archive.folder, '--archive-commit', archive.commit)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['matches'])

        other = self.workspace / 'other-release.json'
        changed_release = self.release | {'source_commit': 'f' * 40}
        other.write_bytes(publication_recovery.encode(changed_release))
        result = run('verify-capture', '--capture', self.capture, '--expected-release', other)
        self.assertEqual(result.returncode, 2)
        self.assertIn('expected V1 release', result.stderr)


class PublicationRecoveryArtifactTests(unittest.TestCase):
    def setUp(self):
        fixture = publish_fixtures.PublishingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.bootstrap()
        self.fixture = fixture
        self.site = fixture.fixture.output
        self.release = json.loads((self.site / 'release.json').read_bytes())
        self.workspace = fixture.fixture.workspace
        self.artifact = self.workspace / 'original-pages.tar'
        with tarfile.open(self.artifact, 'w') as archive:
            archive.add(self.site, arcname='.')
        self.destination = self.workspace / 'captured-from-artifact'

    @staticmethod
    def add_file(archive, name, payload=b'x'):
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def test_original_tar_capture_survives_loss_of_build_directory(self):
        original = {path.relative_to(self.site).as_posix(): path.read_bytes()
                    for path in self.site.rglob('*') if path.is_file()}
        shutil.rmtree(self.site)

        metadata = publication_recovery.capture_v1_artifact(self.artifact, self.release, self.destination)

        verified = verify_capture(self.destination, expected_release=self.release)
        self.assertEqual(metadata, verified)
        captured = {path.relative_to(self.destination / 'site').as_posix(): path.read_bytes()
                    for path in (self.destination / 'site').rglob('*') if path.is_file()}
        self.assertEqual(captured, original)

    def test_rejects_duplicate_traversal_links_oversized_pax_headers_and_excess_tar(self):
        cases = []

        duplicate = self.workspace / 'duplicate.tar'
        cases.append((duplicate, lambda archive: (
            self.add_file(archive, 'duplicate.txt'), self.add_file(archive, 'duplicate.txt'))))
        traversal = self.workspace / 'traversal.tar'
        cases.append((traversal, lambda archive: self.add_file(archive, '../escape')))

        link = self.workspace / 'link.tar'
        def add_link(archive):
            member = tarfile.TarInfo('linked-file')
            member.type = tarfile.SYMTYPE
            member.linkname = '../escape'
            archive.addfile(member)
        cases.append((link, add_link))

        pax = self.workspace / 'oversized-pax.tar'
        def add_oversized_pax(archive):
            member = tarfile.TarInfo('PaxHeaders/file')
            member.type = tarfile.XHDTYPE
            member.size = 4097
            archive.addfile(member, io.BytesIO(b'x' * member.size))
        cases.append((pax, add_oversized_pax))

        for artifact, add_members in cases:
            with self.subTest(artifact=artifact.name), tarfile.open(artifact, 'w') as archive:
                add_members(archive)
            target = self.workspace / (artifact.stem + '-capture')
            with self.assertRaises(RecoveryError):
                publication_recovery.capture_v1_artifact(artifact, self.release, target)
            self.assertFalse(target.exists())

        oversized = self.workspace / 'oversized.tar'
        with oversized.open('wb') as stream:
            stream.truncate(MAX_SITE_BYTES + 16 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(RecoveryError, 'oversized'):
            publication_recovery.capture_v1_artifact(oversized, self.release, self.destination)
        self.assertFalse(self.destination.exists())

    def test_rejects_file_parent_collision_and_release_mismatch(self):
        artifact = self.workspace / 'file-parent.tar'
        with tarfile.open(artifact, 'w') as archive:
            archive.add(self.site, arcname='.')
            self.add_file(archive, 'fake-parent')
            self.add_file(archive, 'fake-parent/child.txt')
        with self.assertRaisesRegex(RecoveryError, 'file/parent'):
            publication_recovery.capture_v1_artifact(artifact, self.release, self.destination)
        self.assertFalse(self.destination.exists())

        wrong_release = dict(self.release, site_sha256='0' * 64)
        with self.assertRaises(RecoveryError):
            publication_recovery.capture_v1_artifact(self.artifact, wrong_release, self.destination)
        self.assertFalse(self.destination.exists())

    def test_release_and_manifest_size_caps_apply_before_any_payload_is_copied(self):
        cases = (
            ('oversized-release.tar', RELEASE, publication_recovery.MAX_RELEASE_BYTES + 1,
             'release metadata.*byte limit'),
            ('oversized-manifest.tar', 'publication.json', publication_recovery.MAX_MANIFEST_BYTES + 1,
             'manifest exceeds its byte limit'),
        )
        for filename, name, size, message in cases:
            artifact = self.workspace / filename
            with tarfile.open(artifact, 'w') as archive:
                self.add_file(archive, name, b'x' * size)
                if name == 'publication.json':
                    self.add_file(archive, RELEASE, b'{}')
            target = self.workspace / (artifact.stem + '-capture')
            with self.subTest(filename=filename), \
                    patch.object(publication_recovery, '_copy_artifact_member',
                                 side_effect=AssertionError('payload read before metadata cap')):
                with self.assertRaisesRegex(RecoveryError, message):
                    publication_recovery.capture_v1_artifact(artifact, self.release, target)
            self.assertFalse(target.exists())

    def test_inventory_limit_counts_implicit_directories_as_well_as_tar_headers(self):
        artifact = self.workspace / 'implicit-directories.tar'
        with tarfile.open(artifact, 'w') as archive:
            self.add_file(archive, RELEASE, b'x')
            self.add_file(archive, 'publication.json', b'x')
            self.add_file(archive, 'alpha/one.wav', b'x')
            self.add_file(archive, 'beta/two.wav', b'x')
        target = self.workspace / 'implicit-directories-capture'
        with patch.object(publication_recovery, 'MAX_INVENTORY_ENTRIES', 4), \
                patch.object(publication_recovery, '_copy_artifact_member',
                             side_effect=AssertionError('payload read before inventory bound')):
            with self.assertRaisesRegex(RecoveryError, 'inventory entry limit'):
                publication_recovery.capture_v1_artifact(artifact, self.release, target)
        self.assertFalse(target.exists())

    def test_cli_captures_from_original_artifact_after_site_loss(self):
        release_path = self.workspace / 'expected-release.json'
        release_path.write_text(json.dumps(self.release))
        shutil.rmtree(self.site)
        with contextlib.redirect_stdout(io.StringIO()):
            result = publication_recovery.main(['capture-v1-artifact', '--artifact', str(self.artifact),
                '--release', str(release_path), '--out', str(self.destination)])
        self.assertEqual(result, 0)
        self.assertEqual(verify_capture(self.destination, expected_release=self.release)['release'], self.release)


if __name__ == '__main__':
    unittest.main()
