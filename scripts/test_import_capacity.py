import hashlib
import json
import shutil
import unittest
from urllib.parse import urlsplit

import build_site
import publish_site
from publication_state import SOURCE_INVENTORY
from test_import_groups import SVG, TABLE, TEXT
import test_publication_state as fixtures
import test_publish_site as publishing_fixtures


class ImportCapacityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_no_change_exit_downloads_no_content_and_leaves_no_candidate_on_clean_runner(self):
        publishing = publishing_fixtures.PublishingTests()
        publishing.setUp()
        self.addCleanup(publishing.doCleanups)
        publishing.bootstrap()
        publishing.fixture.server.requests.clear()
        publishing.fixture.api.calls.clear()
        shutil.rmtree(publishing.fixture.output)
        publishing.prepare()
        self.assertEqual(publishing.state()['status'], 'unchanged')
        self.assertFalse(publishing.fixture.output.exists())
        report = json.loads(publish_site.REPORT.read_bytes())
        self.assertEqual(report['build_status'], 'unchanged')
        self.assertTrue(all(result['transport']['raw_requests'] == 0 for result in report['groups']))
        paths = [urlsplit(request.full_url).path for request in publishing.fixture.server.requests]
        self.assertTrue(all(path.endswith(('/publication.json', '/release.json')) for path in paths))
        self.assertEqual(sum(result['transport']['rest_requests'] for result in report['groups']), 8)

    def test_text_edit_reuses_verified_media_and_fetches_only_changed_text(self):
        fixture = self.fixture
        fixture.bootstrap()
        original = fixture.bytes_at(fixture.live / 'alpha')
        fixture.update('alpha', b'Changed text')
        fixture.server.requests.clear()
        fixture.api.calls.clear()
        build_site.main(import_remote=True, client_factory=fixture.api.client,
                        published_url='https://site.test/course/', allow_unchanged=True)
        report = fixture.report()
        self.assertEqual(report[0]['transport']['raw_requests'], 1)
        self.assertEqual(report[0]['reused_bytes'], len(SVG) + len(TABLE))
        self.assertEqual(report[1]['status'], 'unchanged')
        self.assertEqual((fixture.output / 'alpha/text.md').read_bytes(), b'Changed text')
        self.assertEqual((fixture.output / 'alpha/visual.svg').read_bytes(), original['visual.svg'])
        paths = [urlsplit(request.full_url).path for request in fixture.server.requests]
        self.assertEqual(paths.count('/course/alpha/visual.svg'), 1)
        # Same verified SVG bytes serve the unchanged group without a second request.
        self.assertEqual(paths.count('/course/beta/visual.svg'), 0)

    def test_optional_inventory_absence_or_corruption_falls_back_to_pinned_source(self):
        fixture = self.fixture
        fixture.bootstrap()
        hint = json.loads((fixture.live / SOURCE_INVENTORY).read_bytes())
        hint['groups'] = list(hint['groups'])
        for inventory in (None, b'not json', b'{}', json.dumps(hint).encode()):
            with self.subTest(inventory=inventory):
                target = fixture.live / SOURCE_INVENTORY
                target.unlink(missing_ok=True)
                if inventory is not None:
                    target.write_bytes(inventory)
                fixture.build()
                self.assertTrue(all(result['transport']['raw_requests'] == 4 for result in fixture.report()))
                self.assertEqual(fixture.bytes_at(fixture.output / 'alpha'), fixture.bytes_at(fixture.live / 'alpha'))

    def test_hint_blob_sha_is_never_trusted_in_place_of_byte_verification(self):
        fixture = self.fixture
        fixture.bootstrap()
        hint = json.loads((fixture.live / SOURCE_INVENTORY).read_bytes())
        changed = b'Changed' + b'x' * (len(TEXT) - 7)
        fixture.update('alpha', changed)
        node = fixture.api.routes['/repos/course/alpha/git/trees/' + hashlib.sha1(b'course/alphatree').hexdigest() + '?recursive=1']['tree'][0]
        hint['groups']['alpha']['files']['text.md']['blob_sha'] = node['sha']
        hint['groups']['alpha']['files']['text.md']['size'] = len(changed)
        (fixture.live / SOURCE_INVENTORY).write_text(json.dumps(hint))
        fixture.build()
        self.assertEqual((fixture.output / 'alpha/text.md').read_bytes(), changed)

    def test_policy_change_disables_no_change_shortcuts_and_revalidates_content(self):
        fixture = self.fixture
        fixture.bootstrap()
        fixture.build()
        self.assertTrue(all(result['status'] == 'imported' for result in fixture.report()))
        self.assertTrue(all(result['transport']['rest_requests'] == 4 for result in fixture.report()))

class CohortWorkloadTests(unittest.TestCase):
    def test_six_cycles_of_15_full_inventories_cover_idle_text_and_replacement_workloads(self):
        import tempfile
        from unittest.mock import patch
        from pathlib import Path
        from group_registry import Group
        from import_groups import import_groups
        from publication_state import PublishedSite, snapshot, encode_manifest, encode_source_inventory
        from test_import_groups import FixtureAPI

        groups = [Group(f'group-{i}', f'course/group-{i}', 'main', True, require_visual=False) for i in range(15)]
        for workload in ('unchanged', 'text-only', 'replacement'):
            with self.subTest(workload=workload), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                live = root / 'live'; live.mkdir()
                api = FixtureAPI()

                def revision(cycle):
                    for group in groups:
                        media_cycle = cycle if workload == 'replacement' else 0
                        text_cycle = cycle if workload != 'unchanged' else 0
                        files = {'text.md': f'{group.slug} edit {text_cycle}'.encode(), **{
                            f'media/{i}.png': f'{group.slug} media {i} version {media_cycle}'.encode() for i in range(98)}}
                        old_commit, nodes = api.add(group.repository, files)
                        base = '/repos/' + group.repository
                        commit = hashlib.sha1(json.dumps([(name, data.hex()) for name, data in files.items()]).encode()).hexdigest()
                        old = api.routes.pop(base + '/git/commits/' + old_commit)
                        old['sha'] = commit
                        api.routes[base + '/git/commits/' + commit] = old
                        api.routes[base + '/git/ref/heads/main']['object']['sha'] = commit

                def publish(results, destination):
                    sources = {result['slug']: destination / result['slug'] for result in results}
                    snapshots = {result['slug']: snapshot(sources[result['slug']], result) for result in results}
                    manifest = encode_manifest(snapshots)
                    for slug, source in sources.items():
                        target = live / slug
                        if target.exists(): shutil.rmtree(target)
                        shutil.copytree(source, target)
                    (live / 'publication.json').write_bytes(manifest)
                    (live / SOURCE_INVENTORY).write_bytes(encode_source_inventory(sources, snapshots, manifest))

                revision(0)
                baseline = root / 'baseline'
                results = import_groups(groups, baseline, api.client)
                publish(results, baseline)
                server = fixtures.PublishedHTTP(live)
                with patch('publication_state.build_opener', return_value=server):
                    rest_requests = 0
                    for cycle in range(1, 7):
                        revision(cycle)
                        destination = root / str(cycle)
                        previous = PublishedSite('https://site.test/course/')
                        previous.cache_directory = root / ('cache-' + str(cycle))
                        results = import_groups(groups, destination, api.client, previous=previous, allow_unchanged=True)
                        rest_requests += sum(result['transport']['rest_requests'] for result in results)
                        raw_requests = sum(result['transport']['raw_requests'] for result in results)
                        if workload == 'unchanged':
                            self.assertTrue(all(result['status'] == 'unchanged' for result in results))
                            self.assertEqual(raw_requests, 0)
                            self.assertEqual(previous.metrics['pages_requests'], 1)
                        else:
                            self.assertTrue(all(result['status'] == 'imported' for result in results))
                            # Replacement refetches text and 98 media per group; media.csv is unchanged and reused.
                            self.assertEqual(raw_requests, 15 if workload == 'text-only' else 1485)
                            if workload == 'text-only':
                                self.assertGreater(sum(result['reused_bytes'] for result in results), 0)
                            publish(results, destination)
                    self.assertEqual(rest_requests, 360)
