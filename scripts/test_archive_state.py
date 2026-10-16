import copy
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

import build_site
from archive_state import ArchiveSnapshot, blob_sha, decode_archive, encode_archive, reconcile, unpack_snapshot
from group_registry import Group
from release import PublicationError
from test_fixtures import SVG, archived_group


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bytes = {'text.md': b'Accepted text', 'visual.svg': SVG}
        self.group = Group('alpha', 'course/alpha', 'main', 'live')
        self.groups = {'alpha': archived_group(self.bytes)}

    def snapshot(self, groups=None, data=None):
        groups = groups if groups is not None else self.groups
        folder = self.root / 'snapshot'
        folder.mkdir()
        (folder / 'publication.json').write_bytes(encode_archive(groups))
        for name, content in (data if data is not None else self.bytes).items():
            target = folder / 'alpha' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return folder

    def artifact(self, members):
        path = self.root / 'candidate.tar'
        with tarfile.open(path, 'w') as stream:
            for name, data, kind in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = len(data) if kind == tarfile.REGTYPE else 0
                member.linkname = '../outside' if kind == tarfile.SYMTYPE else ''
                stream.addfile(member, io.BytesIO(data) if member.size else None)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def valid_members(self):
        return [('publication.json', encode_archive(self.groups), tarfile.REGTYPE)] + [
            ('alpha/' + name, content, tarfile.REGTYPE) for name, content in self.bytes.items()]

    def test_snapshot_projects_only_visible_groups_without_source_bytes(self):
        folder = self.snapshot()
        archive = ArchiveSnapshot(folder, 'b' * 40)
        self.assertEqual(archive.read_file('alpha', 'text.md'), b'Accepted text')
        self.assertEqual(archive.served(), {'alpha': {
            'repository': 'course/alpha', 'branch': 'main', 'commit': 'a' * 40, 'require_visual': True,
            'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                      for name, data in self.bytes.items()}}})
        hidden = reconcile(archive.groups, [Group('alpha', 'course/alpha', 'main', 'hidden')])
        (folder / 'publication.json').write_bytes(encode_archive(hidden))
        self.assertEqual(ArchiveSnapshot(folder, 'b' * 40).served(), {})
        self.assertEqual(hidden['alpha']['snapshot'], archive.groups['alpha']['snapshot'])

    def test_archive_mirrors_the_registry(self):
        pilot = Group('pilot', 'course/pilot', 'main', 'hidden')
        groups = reconcile(self.groups, [self.group, pilot])
        self.assertIsNone(groups['pilot']['snapshot'])
        self.assertEqual(groups['alpha']['snapshot'], self.groups['alpha']['snapshot'])
        with self.assertRaisesRegex(PublicationError, 'frozen'):
            reconcile(groups, [self.group, Group('pilot', 'course/pilot', 'main', 'frozen')])
        frozen = reconcile(groups, [Group('alpha', 'course/alpha', 'main', 'frozen'), pilot])
        self.assertEqual(frozen['alpha']['state'], 'frozen')
        with self.assertRaisesRegex(PublicationError, 'keeps its accepted snapshot'):
            reconcile(frozen, [Group('alpha', 'new/alpha', 'main', 'frozen'), pilot])
        self.assertEqual(reconcile(frozen, [pilot]), {'pilot': groups['pilot']})
        self.assertEqual(encode_archive(groups), encode_archive(reconcile(groups, [self.group, pilot])))
        for moved in (Group('alpha', 'new/alpha', 'main', 'live'), Group('alpha', 'course/alpha', 'draft', 'live')):
            with self.subTest(moved=moved):
                self.assertIsNone(reconcile(self.groups, [moved])['alpha']['snapshot'])
        renamed = reconcile(self.groups, [Group('alpha', 'Course/Alpha', 'main', 'live')])
        self.assertEqual(renamed['alpha']['snapshot'], self.groups['alpha']['snapshot'])

    def test_schema_paths_contract_and_exclusion_provenance(self):
        for mutation in ('schema', 'path', 'blob', 'contract', 'old-contract', 'unknown-tree', 'exclusions'):
            with self.subTest(mutation=mutation):
                manifest = {'version': 2, 'groups': copy.deepcopy(self.groups)}
                snapshot = manifest['groups']['alpha']['snapshot']
                if mutation == 'schema': manifest['version'] = 1
                if mutation == 'path': snapshot['files']['../text.md'] = snapshot['files'].pop('text.md')
                if mutation == 'blob': snapshot['files']['text.md']['blob_sha'] = 'wrong'
                if mutation == 'contract': snapshot['contract_version'] = 999
                if mutation == 'old-contract': snapshot['contract_version'] = 1
                if mutation == 'unknown-tree': snapshot['tree'] = None
                if mutation == 'exclusions': snapshot['exclude'] = ['media/original.mov']
                with self.assertRaises(PublicationError): decode_archive(json.dumps(manifest).encode())
        snapshot = self.groups['alpha']['snapshot']
        snapshot['exclude'] = ['media/original.mov']
        snapshot['excluded'] = {'media/original.mov': None}
        self.assertEqual(decode_archive(encode_archive(self.groups)), self.groups)

    def test_full_cohort_long_paths_and_exclusions_fit_the_v2_metadata_bound(self):
        prefix = 'media/' + '/'.join(['a' * 32] * 14) + '/'
        groups = {}
        for number in range(15):
            slug = f'group-{number:02d}'
            entry = copy.deepcopy(self.groups['alpha'])
            entry.update(repository='course/' + slug, require_visual=False)
            excluded = [prefix + f'o{i:03d}.png' for i in range(100)]
            entry['exclude'] = excluded
            snapshot = entry['snapshot']
            snapshot.update(require_visual=False, exclude=excluded, excluded={path: None for path in excluded})
            record = snapshot['files']['text.md']
            snapshot['files'] = {'text.md': record} | {prefix + f'f{i:03d}.png': record for i in range(99)}
            groups[slug] = entry
        payload = encode_archive(groups)
        self.assertGreater(len(payload), 2 * 1024 * 1024)
        self.assertEqual(decode_archive(payload), groups)

    def test_manifest_and_hashes_cannot_hide_extra_files_or_corruption(self):
        folder = self.snapshot()
        (folder / 'alpha/text.md').write_bytes(b'Wrong content')
        with self.assertRaises(PublicationError): ArchiveSnapshot(folder, 'b' * 40)
        (folder / 'alpha/text.md').write_bytes(b'Accepted text')
        (folder / 'script.py').write_text('untrusted')
        with self.assertRaises(PublicationError): ArchiveSnapshot(folder, 'b' * 40)

    def test_verified_artifact_reconstructs_exact_bytes(self):
        path, digest = self.artifact(self.valid_members())
        target = self.root / 'restored'
        unpack_snapshot(path, target, digest)
        archive = ArchiveSnapshot(target, 'b' * 40)
        for name, data in self.bytes.items():
            self.assertEqual(archive.read_file('alpha', name), data)
            self.assertEqual(archive.groups['alpha']['snapshot']['files'][name]['blob_sha'], blob_sha(data))

    def test_artifact_rejects_links_traversal_duplicate_extra_and_corrupt_bytes_atomically(self):
        for mutation in ('link', 'traversal', 'duplicate', 'extra', 'corrupt'):
            with self.subTest(mutation=mutation):
                members = self.valid_members()
                if mutation == 'link': members.append(('alpha/link', b'', tarfile.SYMTYPE))
                if mutation == 'traversal': members.append(('../outside', b'bad', tarfile.REGTYPE))
                if mutation == 'duplicate': members.append(members[-1])
                if mutation == 'extra': members.append(('alpha/script.py', b'bad', tarfile.REGTYPE))
                if mutation == 'corrupt': members[1] = ('alpha/text.md', b'X' * len(self.bytes['text.md']), tarfile.REGTYPE)
                path, digest = self.artifact(members)
                target = self.root / 'rejected'
                with self.assertRaises(PublicationError): unpack_snapshot(path, target, digest)
                self.assertFalse(target.exists())
        path, digest = self.artifact(self.valid_members())
        with self.assertRaises(PublicationError): unpack_snapshot(path, self.root / 'wrong-digest', '0' * 64)

    def test_active_svg_is_rejected_even_with_matching_file_and_transport_hashes(self):
        unsafe = SVG.replace(b'</svg>', b'<script>alert(1)</script></svg>')
        record = self.groups['alpha']['snapshot']['files']['visual.svg']
        record.update(size=len(unsafe), sha256=hashlib.sha256(unsafe).hexdigest(), blob_sha=blob_sha(unsafe))
        self.bytes['visual.svg'] = unsafe
        path, digest = self.artifact(self.valid_members())
        with self.assertRaises(PublicationError): unpack_snapshot(path, self.root / 'active', digest)
        self.assertFalse((self.root / 'active').exists())

    def test_oversized_extension_header_is_rejected_before_tarfile_reads_its_body(self):
        member = tarfile.TarInfo('pax')
        member.type = tarfile.XHDTYPE
        member.size = 10_000_000
        path = self.root / 'oversized-header.tar'
        path.write_bytes(member.tobuf() + b'\0' * 10240)
        with self.assertRaisesRegex(PublicationError, 'archive header'):
            unpack_snapshot(path, self.root / 'rejected-header', hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse((self.root / 'rejected-header').exists())

    def test_shadow_build_is_exact_and_never_reads_source_or_served_site(self):
        groups = copy.deepcopy(self.groups)
        groups['hidden'] = copy.deepcopy(groups['alpha'])
        groups['hidden']['state'] = 'hidden'
        folder = self.snapshot(groups)
        shutil.copytree(folder / 'alpha', folder / 'hidden')
        checkout = self.root / 'trusted-main'
        checkout.mkdir()
        actual_root = Path(__file__).resolve().parent.parent
        for name in ('static', 'editor', 'templates'):
            shutil.copytree(actual_root / name, checkout / name)
        shutil.copy2(actual_root / 'index.html', checkout / 'index.html')
        (checkout / 'groups.json').write_text(json.dumps({'groups': [
            {'slug': 'alpha', 'repository': 'course/alpha', 'state': 'live'},
            {'slug': 'hidden', 'repository': 'course/hidden', 'state': 'hidden'}]}))
        output = self.root / 'site'
        with patch.object(build_site, 'ROOT', checkout), patch.object(build_site, 'OUTPUT', output), \
                patch('release.build_opener', side_effect=AssertionError('Pages must not be read')), \
                patch('github_content.build_opener', side_effect=AssertionError('sources must not be read')):
            first = build_site.build_archive(folder, 'b' * 40)
            files = {path.relative_to(output).as_posix(): path.read_bytes()
                     for path in output.rglob('*') if path.is_file()}
            self.assertFalse((output / 'hidden').exists())
            self.assertEqual(json.loads((output / 'entries.json').read_text()), ['alpha'])
            self.assertEqual(set(json.loads((output / 'publication.json').read_text())['groups']), {'alpha'})
            shutil.rmtree(output)
            self.assertEqual(first, build_site.build_archive(folder, 'b' * 40))
            self.assertEqual(files, {path.relative_to(output).as_posix(): path.read_bytes()
                                     for path in output.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
