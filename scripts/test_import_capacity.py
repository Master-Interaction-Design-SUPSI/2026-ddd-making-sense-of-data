import hashlib
import json
import unittest

from archive_transaction import ArchiveBase
from group_registry import Group
from import_groups import import_groups
from test_fixtures import SVG, TABLE, Course


class ImportCapacityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Course(self)

    def test_text_edit_reuses_archived_media_and_fetches_only_changed_text(self):
        previous = self.fixture.first_archive()
        self.fixture.update('alpha', b'Changed text')
        self.fixture.api.calls.clear()
        results = import_groups(self.fixture.groups(), self.fixture.workspace / 'imports', self.fixture.api.client,
                                previous=ArchiveBase(previous), allow_unchanged=True)
        self.assertEqual(results[0]['transport']['raw_requests'], 1)
        self.assertEqual(results[0]['reused_bytes'], len(SVG) + len(TABLE))
        self.assertEqual(results[1]['status'], 'unchanged')
        self.assertEqual(results[1]['transport']['raw_requests'], 0)
        self.assertEqual((self.fixture.workspace / 'imports/alpha/text.md').read_bytes(), b'Changed text')

    def test_changed_source_bytes_are_downloaded_even_when_sizes_match(self):
        previous = self.fixture.first_archive()
        original = previous.read_file('alpha', 'text.md')
        changed = b'X' * len(original)
        self.fixture.update('alpha', changed)
        results = import_groups(self.fixture.groups(), self.fixture.workspace / 'imports', self.fixture.api.client,
                                previous=ArchiveBase(previous), allow_unchanged=True)
        self.assertEqual(results[0]['status'], 'imported')
        self.assertEqual((self.fixture.workspace / 'imports/alpha/text.md').read_bytes(), changed)


class CohortWorkloadTests(unittest.TestCase):
    def test_full_cohort_idle_text_and_replacement_workloads_reuse_archived_bytes(self):
        fixture = Course(self)
        groups = [Group(f'group-{i}', f'course/group-{i}', 'main', 'live') for i in range(15)]
        (fixture.root / 'groups.json').write_text(json.dumps({'groups': [
            {'slug': group.slug, 'repository': group.repository, 'state': 'live'} for group in groups]}))

        def revision(text_cycle, media_cycle):
            for group in groups:
                files = {'text.md': f'{group.slug} edit {text_cycle}'.encode(), 'visual.svg': SVG, **{
                    f'media/{i}.png': f'{group.slug} media {i} version {media_cycle}'.encode() for i in range(97)}}
                commit = hashlib.sha1(json.dumps([(name, data.hex()) for name, data in files.items()]).encode()).hexdigest()
                fixture.api.add(group.repository, files, commit=commit)

        revision(0, 0)
        previous = fixture.first_archive(groups)
        # One run per workload: idle, a text edit per group, every file replaced.
        for workload, (text_cycle, media_cycle), raw_requests in (
                ('unchanged', (0, 0), 0), ('text-only', (1, 0), 15), ('replacement', (2, 2), 1470)):
            with self.subTest(workload=workload):
                revision(text_cycle, media_cycle)
                results = import_groups(groups, fixture.workspace / workload, fixture.api.client,
                                        previous=ArchiveBase(previous), allow_unchanged=True)
                expected = 'unchanged' if workload == 'unchanged' else 'imported'
                self.assertTrue(all(result['status'] == expected for result in results))
                self.assertEqual(sum(result['transport']['rest_requests'] for result in results), 60)
                # visual.svg and media.csv are unchanged and reused even when every media file is replaced.
                self.assertEqual(sum(result['transport']['raw_requests'] for result in results), raw_requests)
                if workload == 'text-only':
                    self.assertGreater(sum(result['reused_bytes'] for result in results), 0)


if __name__ == '__main__':
    unittest.main()
