import copy
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from archive_state import served_entry
from release import PublicationError, SiteReader, decode_manifest, encode_manifest
from test_fixtures import SVG, TEXT, Response, archived_group


class ServedManifestTests(unittest.TestCase):
    def test_manifest_round_trips_and_rejects_unsafe_paths_bad_hashes_and_duplicate_keys(self):
        original = {'version': 1, 'groups': {'beta': served_entry(archived_group({'text.md': TEXT, 'visual.svg': SVG}))}}
        self.assertEqual(decode_manifest(encode_manifest(original['groups'])), original['groups'])
        for mutation in ("traversal", "hash", "version", "size", "unsupported", "slug"):
            manifest = copy.deepcopy(original)
            entry = manifest["groups"]["beta"]
            if mutation == "traversal":
                entry["files"]["../text.md"] = entry["files"].pop("text.md")
            elif mutation == "hash":
                entry["files"]["text.md"]["sha256"] = "bad hash"
            elif mutation == "version":
                manifest["version"] = True
            elif mutation == "size":
                entry["files"]["text.md"]["size"] = 999999999
            elif mutation == "unsupported":
                entry["files"]["script.js"] = entry["files"]["text.md"]
            else:
                manifest["groups"]["../escaped"] = entry
            with self.subTest(mutation=mutation), self.assertRaises(PublicationError):
                decode_manifest(json.dumps(manifest))
        with self.assertRaises(PublicationError):
            decode_manifest(b'{"version":1,"groups":{},"groups":{}}')


class SiteReaderTests(unittest.TestCase):
    def test_https_canonical_url_and_bounded_requests_without_credentials(self):
        for url in ("http://site.test/", "https://user:pass@site.test/", "https://site.test/?query=yes"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SiteReader(url)
        opener = Mock()
        with patch("release.build_opener", return_value=opener), \
                patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            opener.open.side_effect = HTTPError("url", 302, "redirect", {}, None)
            with self.assertRaises(PublicationError):
                SiteReader("https://site.test/").read("publication.json", 100)
            opener.open.side_effect = None
            opener.open.return_value = Response(b"x" * 101)
            with self.assertRaisesRegex(PublicationError, "byte limit"):
                SiteReader("https://site.test/").read("publication.json", 100)
            opener.open.return_value = Response(b"served")
            self.assertEqual(SiteReader("https://site.test/").read("publication.json", 100), b"served")
        for call in opener.open.call_args_list:
            self.assertFalse(call.args[0].has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
