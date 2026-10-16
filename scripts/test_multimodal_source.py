import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from group_registry import Group
from import_groups import import_groups
from multimodal_source import REPOSITORY, convert, prepare
from content_contract import CONTRACT
from github_content import ImportFailure
from test_fixtures import FixtureAPI


HTML = b'''<html><body><article id="abstract"><strong>Digital technologies</strong>
<span class="red">resources</span><a href="https://example.org/source"><img src="media/1.png" onerror="bad()"></a>
<span class="reference">(\xe2\x96\xa0 Author, 2026. Title)</span>
<audio src="media/5.mp3"></audio><video src="media/4.mp4" autoplay></video>
</article><script>bad()</script></body></html>'''
SOURCES = b'{"media-5":"https://example.org/audio"}'


class MultimodalSourceTests(unittest.TestCase):
    def test_preserves_prose_media_and_sources_without_remote_behavior(self):
        text = convert(HTML.decode(), SOURCES.decode()).decode()
        self.assertIn('**Digital technologies**', text)
        self.assertIn('{#FF09D6|resources}', text)
        self.assertIn('[![Media 1](media/1.png)](https://example.org/source)', text)
        self.assertIn('[[Audio 5|media/5.mp3|source=https://example.org/audio]]', text)
        self.assertIn('[[Author, 2026|Title|type=bibliography]]', text)
        self.assertNotIn('bad()', text)
        self.assertNotIn('autoplay', text)

    def test_rejects_missing_article_unsafe_urls_and_media_paths(self):
        for html in ('<article>Missing id</article>',
                     '<article id="abstract"><script>bad()</script></article>',
                     '<article id="abstract"><a href="javascript:bad()">X</a></article>',
                     '<article id="abstract"><img src="../image.png"></article>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                convert(html, '{}')

    def test_colored_dimensions_and_questions_use_renderable_color_spans(self):
        text = convert('<article id="abstract"><span class="red"><strong>Geology</strong>'
                       '<em>{✶ What does AI consume?}</em></span></article>', '{}',
                       ':root { --red: #112233; }').decode()
        self.assertIn('{#112233|**Geology**}', text)
        self.assertIn('*What does AI consume?*', text)

    def test_imports_and_refreshes_html_brief_from_pinned_sources(self):
        api = FixtureAPI()
        files = {'index.html': HTML, 'sources.json': SOURCES, 'style.css': b':root {--red: #FF09D6;}', 'script.js': b'bad()',
                 'media/1.png': b'image', 'media/5.mp3': b'audio', 'media/4.mp4': b'video'}
        commit, _ = api.add(REPOSITORY, files, table=False)
        group = Group('materialities-of-ai', REPOSITORY, 'main', 'live', require_visual=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = import_groups([group], root / 'first', api.client)[0]
            self.assertEqual(result['status'], 'imported', result)
            self.assertEqual(result['commit'], commit)
            text = (root / 'first' / group.slug / 'text.md').read_text()
            self.assertIn('Digital technologies', text)
            self.assertFalse((root / 'first' / group.slug / 'index.html').exists())
            self.assertFalse((root / 'first' / group.slug / 'script.js').exists())
            files['index.html'] = HTML.replace(b'Digital technologies', b'Updated technologies')
            api.add(REPOSITORY, files, table=False)
            result = import_groups([group], root / 'second', api.client)[0]
            self.assertEqual(result['status'], 'imported', result)
            self.assertIn('Updated technologies', (root / 'second' / group.slug / 'text.md').read_text())

    def test_other_html_repositories_still_require_text_markdown(self):
        api = FixtureAPI()
        api.add('other/html', {'index.html': HTML, 'sources.json': SOURCES}, table=False)
        with tempfile.TemporaryDirectory() as temporary:
            result = import_groups([Group('other', 'other/html', 'main', 'live', require_visual=False)],
                                   Path(temporary), api.client)[0]
            self.assertEqual(result['status'], 'failed')
            self.assertIn('requires text.md', result['error'])

    def test_preserves_video_source_and_underlined_data_labels(self):
        text = convert('<article id="abstract"><u>Dataset XX</u>'
                       '<a href="https://example.org/video"><video src="media/4.mp4"></video></a>'
                       '</article>', '{}').decode()
        self.assertIn('{underline|Dataset XX}', text)
        self.assertIn('[[Video 4|media/4.mp4|source=https://example.org/video]]', text)
        self.assertNotIn('[Video source]', text)


class AdapterBoundaryTests(unittest.TestCase):
    def test_native_markdown_wins_without_downloading_adapter_sources(self):
        api = FixtureAPI()
        text = b'---\ntitle: Native brief\n---\nNative content'
        api.add(REPOSITORY, {'text.md': text, 'index.html': b'invalid HTML'}, table=False)
        group = Group('brief', REPOSITORY, 'main', 'live', require_visual=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = import_groups([group], root, api.client)[0]
            # A native document follows ordinary admission, including media.csv.
            self.assertEqual(result['status'], 'failed')
            self.assertIn('media.csv', result['error'])
        api.add(REPOSITORY, {'text.md': text, 'index.html': b'invalid HTML'})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = import_groups([group], root, api.client)[0]
            self.assertEqual(result['status'], 'imported', result)
            self.assertEqual((root / 'brief/text.md').read_bytes(), text)

    def test_invalid_listing_is_rejected_before_source_downloads(self):
        from import_groups import validate_tree
        group = Group('brief', REPOSITORY, 'main', 'live', require_visual=False)
        client = Mock()
        tree = {'truncated': True, 'tree': []}
        with self.assertRaises(ImportFailure):
            prepare(group, tree, client, 'a' * 40, validate_tree=validate_tree)
        client.raw_file.assert_not_called()

    def test_sources_are_size_bounded_hash_verified_and_pinned_to_one_commit(self):
        group = Group('brief', REPOSITORY, 'main', 'live', require_visual=False)
        payloads = {'index.html': b'<article id="abstract">Brief</article>',
                    'sources.json': b'{}', 'style.css': b':root {}'}
        nodes = [{'path': name, 'size': len(data), 'sha': hashlib.sha1(
            f'blob {len(data)}\0'.encode() + data).hexdigest()}
                 for name, data in payloads.items()]
        client = Mock()
        client.raw_file.side_effect = lambda repository, commit, name, size: payloads[name]
        _, generated = prepare(group, {'tree': nodes}, client, 'a' * 40, validate_tree=Mock())
        self.assertIn(b'Brief', generated['text.md'])
        for call in client.raw_file.call_args_list:
            self.assertEqual(call.args[:2], (REPOSITORY, 'a' * 40))
        client.raw_file.reset_mock()
        nodes[0]['size'] = CONTRACT['limits']['text_bytes'] + 1
        with self.assertRaisesRegex(ValueError, 'oversized index.html'):
            prepare(group, {'tree': nodes}, client, 'a' * 40, validate_tree=Mock())
        client.raw_file.assert_not_called()
        nodes[0]['size'] = len(payloads['index.html'])
        nodes[0]['sha'] = '0' * 40
        with self.assertRaisesRegex(ImportFailure, 'bytes differ'):
            prepare(group, {'tree': nodes}, client, 'a' * 40, validate_tree=Mock())
