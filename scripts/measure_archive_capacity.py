"""Manual, local-only archive capacity rehearsal (all timings are simulated/local).

Run the default smoke profile for a quick API/import/build/Git check. The explicit
``near-limit`` profile models 15 groups x 100 selected files over six cycles and
requires several GiB of free scratch space. No GitHub endpoints are contacted.
"""

import argparse
import contextlib
import hashlib
import io
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

import archive_git
import archive_transaction
import build_site
from archive_state import ArchiveSnapshot, encode_archive, group_record
from github_content import GitHubClient
from group_registry import Group


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080"/></svg>'
TEXT = b'---\ntitle: Capacity fixture\nauthors: Test group\n---\nA media workload fixture.\n'
SCENARIOS = ('cold', 'idle', 'text-edit', 'full-replacement-1', 'idle', 'full-replacement-2')
CHUNK = 65536


class FileResponse:
    """urllib-compatible stream that keeps large fake raw responses on disk."""

    def __init__(self, path):
        self.path = Path(path)
        self.stream = self.path.open('rb')
        self.headers = {'Content-Length': str(self.path.stat().st_size)}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.stream.close()

    def read1(self, size=-1):
        return self.stream.read(size)


class MemoryResponse(io.BytesIO):
    headers = {}


class FixtureAPI:
    """GitHub-shaped metadata with raw blobs streamed from an isolated fixture tree."""

    def __init__(self, source):
        self.source = Path(source)
        self.metadata = {}
        self.blobs = {}
        self.calls = []
        self.refresh()

    @staticmethod
    def git_blob_sha(path):
        size = path.stat().st_size
        digest = hashlib.sha1(f'blob {size}\0'.encode())
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def refresh(self):
        self.metadata.clear()
        self.blobs.clear()
        for group_dir in sorted(path for path in self.source.iterdir() if path.is_dir()):
            repository = f'course/{group_dir.name}'
            digest = hashlib.sha256()
            pending = []
            for path in sorted(path for path in group_dir.rglob('*') if path.is_file()):
                name = path.relative_to(group_dir).as_posix()
                sha = self.git_blob_sha(path)
                digest.update(name.encode() + b'\0' + str(path.stat().st_size).encode() + b'\0' + sha.encode())
                pending.append((path, name, sha))
            commit = hashlib.sha1((repository + ':commit:' + digest.hexdigest()).encode()).hexdigest()
            tree_sha = hashlib.sha1((repository + ':tree:' + commit).encode()).hexdigest()
            base = '/repos/' + repository
            nodes = []
            for path, name, sha in pending:
                nodes.append({'path': name, 'mode': '100644', 'type': 'blob',
                              'size': path.stat().st_size, 'sha': sha})
                self.blobs[(repository, commit, name)] = path
            self.metadata[base] = {'private': False, 'visibility': 'public', 'full_name': repository}
            self.metadata[base + '/git/ref/heads/main'] = {
                'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': commit}}
            self.metadata[base + '/git/commits/' + commit] = {'sha': commit, 'tree': {'sha': tree_sha}}
            self.metadata[base + '/git/trees/' + tree_sha + '?recursive=1'] = {
                'sha': tree_sha, 'tree': nodes, 'truncated': False}

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        path = url.path + ('?' + url.query if url.query else '')
        self.calls.append(path)
        if url.hostname == 'raw.githubusercontent.com':
            parts = unquote(url.path).lstrip('/').split('/')
            repository, commit = '/'.join(parts[:2]), parts[2]
            name = '/'.join(parts[3:])
            target = self.blobs.get((repository, commit, name))
            if target is None:
                raise HTTPError(request.full_url, 404, 'not found', {}, None)
            return FileResponse(target)
        value = self.metadata.get(path)
        if value is None:
            raise HTTPError(request.full_url, 404, 'not found', {}, None)
        payload = json.dumps(value, separators=(',', ':')).encode()
        return MemoryResponse(payload)

    def client(self):
        client = GitHubClient()
        client.opener = self
        return client


def wav_header(data_bytes):
    return (b'RIFF' + (36 + data_bytes).to_bytes(4, 'little') + b'WAVEfmt ' + (16).to_bytes(4, 'little')
            + (1).to_bytes(2, 'little') + (1).to_bytes(2, 'little') + (22050).to_bytes(4, 'little')
            + (44100).to_bytes(4, 'little') + (2).to_bytes(2, 'little') + (16).to_bytes(2, 'little')
            + b'data' + data_bytes.to_bytes(4, 'little'))


def write_noisy_wav(path, total_bytes, seed):
    payload_size = total_bytes - 44
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    with path.open('wb') as stream:
        stream.write(wav_header(payload_size))
        remaining = payload_size
        while remaining:
            chunk = rng.randbytes(min(CHUNK, remaining))
            stream.write(chunk)
            remaining -= len(chunk)


def seed_group(folder, profile, group_index, version):
    folder.mkdir(parents=True, exist_ok=True)
    text = TEXT if version == 0 else TEXT.replace(b'fixture', f'fixture revision {version}'.encode())
    (folder / 'text.md').write_bytes(text)
    (folder / 'visual.svg').write_bytes(SVG)
    if profile == 'near-limit':
        # 97 media + text.md + visual.svg + media.csv fill the 100-file limit.
        sizes = [20 * 1024 * 1024, 20 * 1024 * 1024] + [108900] * 95
    else:
        sizes = [131072] * 4
    rows = ['name,source,file']
    for index, size in enumerate(sizes, start=1):
        write_noisy_wav(folder / 'media' / f'clip-{index:03}.wav', size,
                        seed=f'{profile}:{group_index}:{index}:{version}'.encode())
        rows.append(f'clip {index:03},,media/clip-{index:03}.wav')
    (folder / 'media.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8')


def make_registry(group_count):
    return [Group(f'group-{index:02}', f'course/group-{index:02}', 'main', True)
            for index in range(1, group_count + 1)]


def file_bytes(folder):
    return sum(path.stat().st_size for path in Path(folder).rglob('*') if path.is_file())


def object_directory_bytes(folder):
    objects = Path(folder) / 'objects'
    return sum(path.stat().st_size for path in objects.rglob('*') if path.is_file())


def reachable_object_bytes(repository):
    listed = archive_git.git(repository, 'rev-list', '--objects', '--all').decode().splitlines()
    object_ids = sorted({line.split(' ', 1)[0] for line in listed if line})
    if not object_ids:
        return 0
    result = subprocess.run(['git', '-C', str(repository), 'cat-file', '--batch-check=%(objectsize)'],
        input=('\n'.join(object_ids) + '\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=archive_git.environment(), timeout=120, check=True)
    return sum(int(line) for line in result.stdout.splitlines())


def copy_site_sources(checkout):
    repo = Path(__file__).resolve().parent.parent
    for name in ('editor', 'templates', 'static'):
        shutil.copytree(repo / name, checkout / name)
    shutil.copy2(repo / 'index.html', checkout / 'index.html')
    (checkout / '.github').mkdir(exist_ok=True)
    shutil.copy2(repo / '.github/publishing.json', checkout / '.github/publishing.json')


def measure(profile='smoke', *, output=None, scratch=None):
    if profile not in {'smoke', 'near-limit'}:
        raise ValueError('profile must be smoke or near-limit')
    group_count = 15 if profile == 'near-limit' else 2
    media_count = 98 if profile == 'near-limit' else 4
    if profile == 'near-limit':
        scratch_path = Path(scratch or tempfile.gettempdir())
        free = shutil.disk_usage(scratch_path).free
        required = 8 * 1024 ** 3
        if free < required:
            raise RuntimeError(f'near-limit rehearsal needs at least {required} free scratch bytes; found {free}')
    if output is not None and (Path(output).exists() or Path(output).is_symlink()):
        raise FileExistsError('report output must be a new file')
    scratch_root = Path(scratch or tempfile.gettempdir())
    scratch_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cycles = []
    with tempfile.TemporaryDirectory(prefix='archive-capacity-', dir=scratch_root) as temporary:
        work = Path(temporary)
        source = work / 'source'
        source.mkdir()
        registry = make_registry(group_count)
        source_generation = time.monotonic()
        for number, _group in enumerate(registry, start=1):
            seed_group(source / _group.slug, profile, number, 0)
        source_generation_seconds = time.monotonic() - source_generation
        api = FixtureAPI(source)

        initial = work / 'initial-archive'
        initial.mkdir()
        (initial / 'publication.json').write_bytes(encode_archive({group.slug: group_record(group) for group in registry}))
        previous = ArchiveSnapshot(initial, '0' * 40)

        checkout = work / 'checkout'
        checkout.mkdir()
        (checkout / '.github').mkdir()
        copy_site_sources(checkout)
        registry_json = {'groups': [{'slug': group.slug, 'repository': group.repository,
                                     'branch': group.branch, 'enabled': True} for group in registry]}
        (checkout / 'groups.json').write_text(json.dumps(registry_json))
        site_output = work / 'site'
        previous_folder = None
        previous_commit = None
        bare = work / 'archive.git'
        archive_git.git(work, 'init', '--bare', '--quiet', str(bare))
        local_repo = work / 'writer'
        local_repo.mkdir()
        archive_git.git(local_repo, 'init', '--quiet')
        archive_git.git(local_repo, 'remote', 'add', 'origin', str(bare))
        archive_git.git(bare, 'symbolic-ref', 'HEAD', archive_git.ARCHIVE_REF)
        applied_versions = [0, 0, 1, 2, 2, 3]

        for cycle_index, (scenario, version) in enumerate(zip(SCENARIOS, applied_versions), start=1):
            print('capacity-cycle=' + json.dumps({'cycle': cycle_index, 'scenario': scenario, 'status': 'starting'},
                  sort_keys=True), file=sys.stderr, flush=True)
            generation_seconds = 0.0
            metadata_rebuild_seconds = 0.0
            if scenario in {'text-edit', 'full-replacement-1', 'full-replacement-2'}:
                stamp = time.monotonic()
                for number, group in enumerate(registry, start=1):
                    folder = source / group.slug
                    if scenario == 'text-edit':
                        (folder / 'text.md').write_bytes(TEXT.replace(b'fixture', b'fixture text edit'))
                    else:
                        seed_group(folder, profile, number, version)
                generation_seconds = time.monotonic() - stamp
                metadata_started = time.monotonic()
                api.refresh()
                metadata_rebuild_seconds = time.monotonic() - metadata_started

            archive_reads_before = time.monotonic()
            destination = work / f'candidate-{cycle_index}'
            prior_fixture_calls = len(api.calls)
            projection_seconds = [0.0]
            original_projection = archive_transaction.projected_site

            def timed_projection(folder):
                tick = time.monotonic()
                try:
                    return original_projection(folder)
                finally:
                    projection_seconds[0] += time.monotonic() - tick

            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                     patch.object(build_site, 'ROOT', checkout), \
                     patch.object(build_site, 'OUTPUT', site_output), \
                     patch.object(archive_transaction, 'projected_site', timed_projection):
                    report = archive_transaction.prepare_archive(registry, previous, destination,
                        client_factory=api.client, allow_unchanged=True, allow_idle=True)
            finally:
                archive_transaction.projected_site = original_projection
            import_seconds = time.monotonic() - archive_reads_before
            transport = report['transport']
            client_calls = api.calls[prior_fixture_calls:]
            # Fixture API call paths are counted by the exact GitHubClient counters in report;
            # these values are response-body bytes only, with no headers/TLS/network latency.
            row = {'cycle': cycle_index, 'scenario': scenario, 'simulated_source_version': version,
                   'status': 'idle' if report['idle'] else 'prepared', 'source_generation_seconds': round(generation_seconds, 3),
                   'source_metadata_rebuild_seconds': round(metadata_rebuild_seconds, 3),
                   'prepare_seconds': round(import_seconds, 3), 'projected_site_seconds': round(projection_seconds[0], 3),
                   'input_bytes': file_bytes(source), 'site_bytes': (report.get('projected_site') or {}).get('site_bytes'),
                   'source_selected_bytes_after_cycle': sum(path.stat().st_size for path in source.rglob('*') if path.is_file()),
                   'rest_requests': transport['rest_requests'], 'rest_body_bytes': transport['rest_bytes'],
                   'raw_requests': transport['raw_requests'], 'raw_body_bytes': transport['raw_bytes'],
                   'verified_reuse_bytes': transport['archive_read_bytes'],
                   'per_group': [{'slug': result['slug'], 'status': result['status'],
                                  'elapsed_seconds': result.get('transport', {}).get('elapsed_seconds'),
                                  'rest_requests': result.get('transport', {}).get('rest_requests', 0),
                                  'raw_requests': result.get('transport', {}).get('raw_requests', 0),
                                  'raw_body_bytes': result.get('transport', {}).get('raw_bytes', 0),
                                  'verified_reuse_bytes': result.get('reused_bytes', 0)}
                                 for result in report['groups']],
                   'idle': bool(report['idle']), 'fixture_http_calls': len(client_calls)}

            if not report['idle']:
                candidate = ArchiveSnapshot(destination, previous.commit if previous else '0' * 40)
                tar_path = work / f'candidate-{cycle_index}.tar'
                pack_started = time.monotonic()
                archive_transaction.pack_snapshot(destination, tar_path)
                row['candidate_tar_bytes'] = tar_path.stat().st_size
                row['candidate_pack_seconds'] = round(time.monotonic() - pack_started, 3)
                before_remote = object_directory_bytes(bare)
                commit_started = time.monotonic()
                commit = archive_git.create_commit(local_repo, destination, previous_commit,
                    main_commit='1' * 40, authority='v2')
                row['archive_commit_construction_seconds'] = round(time.monotonic() - commit_started, 3)
                push_started = time.monotonic()
                archive_git.git(local_repo, 'push', 'origin', commit + ':' + archive_git.ARCHIVE_REF)
                row['local_git_push_seconds'] = round(time.monotonic() - push_started, 3)
                after_remote = object_directory_bytes(bare)
                row['archive_commit'] = commit
                row['local_remote_object_disk_growth_bytes'] = after_remote - before_remote
                row['reachable_history_object_bytes'] = reachable_object_bytes(bare)
                if previous_folder:
                    shutil.rmtree(previous_folder)
                previous_folder = destination
                previous_commit = commit
                previous = ArchiveSnapshot(destination, commit)
                probe = work / f'probe-{cycle_index}.git'
                archive_git.git(work, 'init', '--bare', '--quiet', str(probe))
                archive_git.git(probe, 'remote', 'add', 'origin', str(bare))
                fetch_started = time.monotonic()
                proxy_bytes = archive_git.fetch_commit(probe, commit)
                row['fresh_shallow_fetch_seconds'] = round(time.monotonic() - fetch_started, 3)
                row['fresh_shallow_fetch_git_objects_bytes_proxy_not_wire'] = proxy_bytes
                row['fresh_fetch_local_object_directory_bytes'] = object_directory_bytes(probe)
                expansion = work / f'fetched-snapshot-{cycle_index}'
                expansion_started = time.monotonic()
                archive_git.snapshot_from_git(probe, commit, expansion)
                row['fresh_snapshot_unpack_and_verify_seconds'] = round(time.monotonic() - expansion_started, 3)
                row['fresh_snapshot_logical_bytes'] = file_bytes(expansion)
                shutil.rmtree(expansion)
                shutil.rmtree(probe)
                (work / f'candidate-{cycle_index}.tar').unlink()
            cycles.append(row)
            print(
                'capacity-cycle='
                + json.dumps({'cycle': cycle_index, 'scenario': scenario, 'status': row['status'],
                    'source_generation_seconds': row['source_generation_seconds'],
                    'metadata_rebuild_seconds': row['source_metadata_rebuild_seconds'],
                    'prepare_seconds': row['prepare_seconds'], 'projection_seconds': row['projected_site_seconds'],
                    'pack_seconds': row.get('candidate_pack_seconds'),
                    'commit_seconds': row.get('archive_commit_construction_seconds'),
                    'push_seconds': row.get('local_git_push_seconds'),
                    'fetch_seconds': row.get('fresh_shallow_fetch_seconds'),
                    'unpack_verify_seconds': row.get('fresh_snapshot_unpack_and_verify_seconds')}, sort_keys=True),
                file=sys.stderr, flush=True)

    summary = {'measurement_kind': 'local_and_simulated_only',
        'network_access': 'none; fixture API is file-backed and the Git remote is a local bare repository',
        'wire_metrics_warning': 'REST/raw counts and body bytes exclude headers/TLS; Git object-directory bytes are a transfer proxy, not wire bytes',
        'profile': profile, 'groups': group_count, 'selected_files_per_group': (100 if profile == 'near-limit' else 6),
        'media_files_per_group': media_count, 'cycles': cycles,
        'fixture_source_generation_seconds': round(source_generation_seconds, 3),
        'total_seconds_including_fixture_generation': round(time.monotonic() - started, 3),
        'logical_cohort_input_bytes': max((cycle['input_bytes'] for cycle in cycles), default=0),
        'logical_input_bytes_per_group': (max((cycle['input_bytes'] for cycle in cycles), default=0) // group_count),
        'limits': {'groups': 15, 'files_per_group': 100, 'group_bytes': 50 * 1024 * 1024,
                   'site_bytes': 900 * 1024 * 1024, 'import_wall_seconds': 15 * 60,
                   'group_wall_seconds': 180, 'archive_shallow_fetch_seconds': 180}}
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('smoke', 'near-limit'), default='smoke')
    parser.add_argument('--out', help='write JSON report to a new path')
    parser.add_argument('--scratch', help='parent directory for the isolated temporary workspace')
    args = parser.parse_args()
    print(json.dumps(measure(args.profile, output=args.out, scratch=args.scratch), indent=2))


if __name__ == '__main__':
    main()
