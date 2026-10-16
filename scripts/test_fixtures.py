"""Shared fixtures: GitHub fakes, a trusted course checkout, its served site and its group repositories."""

import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

import build_site
from archive_state import ArchiveSnapshot, empty_archive, file_record
from archive_transaction import prepare_archive
from content_contract import CONTRACT_VERSION
from github_content import GitHubClient
from group_registry import load_registry

URL = 'https://site.test/course/'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="10" height="10"/></svg>'
TEXT = b'---\ntitle: Fixture\nauthors: Test group\n---\n*Italic* and {crimson|color}. [[Detail|media/detail.svg]]'


class Response(io.BytesIO):
    headers = {}


def with_table(files):
    """Add the media.csv every imported group needs: one row per media file."""
    if "media.csv" in files:
        return files
    rows = ["name,source,file"] + [f"{name},,{name}" for name in sorted(files) if name not in {"text.md", "visual.svg"}]
    return {**files, "media.csv": ("\n".join(rows) + "\n").encode()}


TABLE = with_table({"media/detail.svg": b""})["media.csv"]


class FixtureAPI:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, repository, files=None, branch="main", table=True, commit=None):
        """Make files the branch head at commit; by default one fixed commit per repository."""
        files = files or {"text.md": TEXT, "visual.svg": SVG, "media/detail.svg": SVG}
        if table:
            files = with_table(files)
        base = "/repos/" + repository
        commit = commit or hashlib.sha1(repository.encode()).hexdigest()
        tree = hashlib.sha1((repository + "tree").encode()).hexdigest()
        self.routes[base] = dict(private=False, visibility="public", full_name=repository)
        self.routes[base + "/git/ref/heads/" + branch] = {"ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": commit}}
        self.routes[base + "/git/commits/" + commit] = {"sha": commit, "tree": {"sha": tree}}
        nodes = []
        for name, data in files.items():
            sha = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            nodes.append(dict(path=name, mode="100644", type="blob", size=len(data), sha=sha))
            self.routes[base + "/git/blobs/" + sha] = data
        self.routes[base + "/git/trees/" + tree + "?recursive=1"] = {"sha": tree, "tree": nodes, "truncated": False}
        return commit, nodes

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        path = url.path + ("?" + url.query if url.query else "")
        self.calls.append(path)
        if url.hostname == "raw.githubusercontent.com":
            parts = unquote(path).lstrip("/").split("/")
            base = "/repos/" + "/".join(parts[:2])
            commit = self.routes.get(base + "/git/commits/" + parts[2], {})
            tree = self.routes.get(base + "/git/trees/" + commit.get("tree", {}).get("sha", "") + "?recursive=1", {})
            node = next((node for node in tree.get("tree", []) if node["path"] == "/".join(parts[3:])), None)
            path = base + "/git/blobs/" + node["sha"] if node else path
        if path not in self.routes:
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        data = self.routes[path]
        if isinstance(data, Exception):
            raise data
        return Response(data if isinstance(data, bytes) else json.dumps(data).encode())

    def client(self):
        client = GitHubClient()
        client.opener = self
        return client


def archived_group(files, repository='course/alpha', state='live'):
    """One archive row whose accepted snapshot holds these files."""
    return {'repository': repository, 'branch': 'main', 'state': state, 'exclude': [],
            'snapshot': {'commit': 'a' * 40, 'tree': 'b' * 40, 'contract_version': CONTRACT_VERSION,
                         'exclude': [], 'excluded': {},
                         'files': {name: file_record(data) for name, data in files.items()}}}


class PublishedHTTP:
    """A deployed folder served independently of disposable build directories."""

    def __init__(self, folder):
        self.folder = folder
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        path = self.folder / urlsplit(request.full_url).path.removeprefix('/course/')
        if not path.is_file():
            raise HTTPError(request.full_url, 404, 'not published', {}, None)
        return Response(path.read_bytes())


class Course:
    """Course checkout with groups alpha and beta, both live, and their source repositories."""

    def __init__(self, test):
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / 'checkout'
        self.root.mkdir()
        self.output = self.root / '_site'
        self.live = self.workspace / 'live'
        self.live.mkdir()
        self.server = PublishedHTTP(self.live)
        self.api = FixtureAPI()
        for patcher in (patch.object(build_site, 'ROOT', self.root), patch.object(build_site, 'OUTPUT', self.output),
                        patch('release.build_opener', return_value=self.server)):
            patcher.start()
            test.addCleanup(patcher.stop)
        (self.root / 'index.html').write_text('trusted code')
        (self.root / 'static').mkdir()
        assets = Path(__file__).resolve().parent.parent
        shutil.copytree(assets / 'editor', self.root / 'editor')
        shutil.copytree(assets / 'templates', self.root / 'templates')
        self.registry('alpha', 'beta')
        for name in ('alpha', 'beta'):
            self.api.add('course/' + name)

    def registry(self, *names, hidden=()):
        groups = [{'slug': name, 'repository': 'course/' + name, 'state': 'hidden' if name in hidden else 'live'}
                  for name in names]
        (self.root / 'groups.json').write_text(json.dumps({'groups': groups}))

    def groups(self):
        return load_registry(self.root / 'groups.json')

    def first_archive(self, registry=None, commit='a' * 40):
        """Import every live group into a new archive, as a first run accepts it."""
        destination = self.workspace / 'first-archive'
        # The fixture needs the archive, not the projected site build.
        with patch('archive_transaction.projected_site'):
            prepare_archive(registry or self.groups(), empty_archive(self.workspace / 'empty-archive'), destination,
                            client_factory=self.api.client)
        return ArchiveSnapshot(destination, commit)

    def deploy(self):
        shutil.rmtree(self.live)
        shutil.copytree(self.output, self.live)

    @staticmethod
    def bytes_at(folder):
        return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob('*') if path.is_file()}

    def update(self, name, text=b'new content', visual=SVG):
        """Commit a new revision; it keeps the media.csv of the first revision."""
        revision = hashlib.sha1(text + visual).hexdigest()
        self.api.add('course/' + name, {'text.md': text, 'visual.svg': visual, 'media.csv': TABLE}, commit=revision)
        return revision
