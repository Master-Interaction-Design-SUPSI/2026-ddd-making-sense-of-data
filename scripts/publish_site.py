"""Prepare, record and verify one serialized GitHub Pages publication."""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError

import build_site
from deployment_records import DeploymentRecords
from publication_state import MANIFEST, MAX_MANIFEST_BYTES, RecoveryError, SiteReader, decode_manifest


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".scratch/publication-run.json"
REPORT = ROOT / ".scratch/group-import-report.json"
RELEASE = "release.json"


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def validate_release(value):
    fields = {"version", "run_id", "run_attempt", "source_commit", "source_tree", "site_sha256", "manifest_sha256"}
    v2 = {'version', 'run_id', 'run_attempt', 'main_commit', 'main_tree', 'archive_commit',
          'archive_manifest_sha256', 'served_manifest_sha256', 'site_sha256', 'probes'}
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] not in (1, 2) or set(value) != (fields if value['version'] == 1 else v2)):
        raise RecoveryError("Invalid publication record; do not bypass the freshness check")
    for key in ("run_id", "run_attempt"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[1-9][0-9]*", value[key]):
            raise RecoveryError("Invalid publication run identity")
    digests = (("source_commit", 40), ("source_tree", 40), ("site_sha256", 64), ("manifest_sha256", 64)) if value['version'] == 1 else (
        ('main_commit', 40), ('main_tree', 40), ('archive_commit', 40), ('archive_manifest_sha256', 64),
        ('served_manifest_sha256', 64), ('site_sha256', 64))
    for key, length in digests:
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value[key]):
            raise RecoveryError("Invalid publication digest: " + key)
    if value['version'] == 2:
        probes = value['probes']
        if (not isinstance(probes, list) or len(probes) > 16
                or any(not isinstance(path, str) or len(path) > 576 or len(path.split('/')) < 2
                       or not all(re.fullmatch(r'[a-z0-9][a-z0-9._-]*', part) for part in path.split('/'))
                       for path in probes) or len(set(probes)) != len(probes)):
            raise RecoveryError('Invalid publication content probes')
    if len(encode(value)) > 4096:
        raise RecoveryError('Publication record exceeds its served byte limit')
    return value


def served_release(url, expected, attempts=12, delay=10, *, metrics=None):
    validate_release(expected)
    error = "Publication is not served yet"
    deadline = time.monotonic() + 180
    for attempt in range(attempts):
        if time.monotonic() >= deadline:
            break
        reader = SiteReader(url)
        try:
            reader.overall_deadline = deadline
            release = json.loads(reader.read(RELEASE, 4096))
            manifest = reader.read(MANIFEST, MAX_MANIFEST_BYTES)
            manifest_hash = expected['manifest_sha256'] if expected['version'] == 1 else expected['served_manifest_sha256']
            if release != expected or digest(manifest) != manifest_hash:
                raise RecoveryError("Served publication differs from the latest deployment record")
            groups = decode_manifest(manifest)
            if expected['version'] == 2:
                for path in expected['probes']:
                    slug, name = path.split('/', 1)
                    record = groups.get(slug, {}).get('files', {}).get(name)
                    if record is None:
                        raise RecoveryError('Verification probe is outside the served inventory')
                    content = reader.read(path, record['size'])
                    if len(content) != record['size'] or digest(content) != record['sha256']:
                        raise RecoveryError('Served content probe differs from accepted snapshot: ' + path)
            if json.loads(reader.read(RELEASE, 4096)) != expected:
                raise RecoveryError("Served publication changed during verification")
            return groups
        except (RecoveryError, ValueError) as failure:
            error = str(failure)
        finally:
            if metrics is not None:
                for key, count in reader.metrics.items():
                    metrics[key] = metrics.get(key, 0) + count
        if attempt + 1 < attempts:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(delay, remaining))
    raise RecoveryError(error + "; publication is blocked. See MAINTAINERS.md for recovery.")


def require_unpublished(url):
    for name, limit in ((MANIFEST, MAX_MANIFEST_BYTES), (RELEASE, 4096)):
        try:
            SiteReader(url).read(name, limit)
        except RecoveryError as error:
            if isinstance(error.__cause__, HTTPError) and error.__cause__.code == 404:
                continue
            raise
        raise RecoveryError("Imported publication already exists; first publication is not allowed")


def site_digest(folder):
    records = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("The publication must not contain symlinks")
        if not path.is_file() or path.relative_to(folder).as_posix() == RELEASE:
            continue
        checksum = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                checksum.update(chunk)
        records.append([path.relative_to(folder).as_posix(), path.stat().st_size, checksum.hexdigest()])
    return digest(encode(records))


def git_revision(ref):
    return subprocess.check_output(["git", "rev-parse", ref], cwd=ROOT, text=True).strip()


def worktree_clean():
    return not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                       cwd=ROOT, text=True).strip()


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_bytes(encode(state))


def output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def run_url(release):
    return f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{release['run_id']}"


def prepare(records, url, first_publication=False, retry_publication=False):
    state = {"status": "preparing", "url": url}
    save_state(state)
    REPORT.unlink(missing_ok=True)
    output("deploy", "false")
    output("retry", "false")
    try:
        if first_publication and retry_publication:
            raise ValueError("Choose initial rollout or retry, not both")
        if retry_publication:
            latest = records.latest()
            if not latest:
                raise RecoveryError("There is no recorded candidate to retry")
            release = validate_release(latest["payload"])
            if release['version'] == 2:
                raise RecoveryError('Archive release requires durable main/archive reconstruction, not V1 artifact retry')
            state.update(status="retrying", release=release, deployment_id=latest["id"])
            save_state(state)
            output("retry", "true")
            output("artifact_run", release["run_id"])
            output("artifact_attempt", release["run_attempt"])
            return
        commit = git_revision("HEAD")
        if commit != records.main_commit():
            state["status"] = "superseded"
            save_state(state)
            return
        latest = records.latest()
        previous = None
        previous_groups = {}
        if latest:
            if first_publication:
                raise RecoveryError("A publication record already exists; do not use first publication")
            previous = validate_release(latest["payload"])
            if previous['version'] == 2:
                raise RecoveryError('Archive authority is active; use archive publication or the rehearsed V1 rollback')
            previous_groups = served_release(url, previous)
        else:
            if not first_publication:
                raise RecoveryError("No publication record. A maintainer must run the initial publication explicitly")
            require_unpublished(url)
        build = build_site.main(import_remote=True, report_path=REPORT,
                        published_url=url if previous else None, first_publication=previous is None,
                        expected_manifest_sha256=previous["manifest_sha256"] if previous else None,
                        allow_unchanged=bool(previous and previous["source_tree"] == git_revision("HEAD^{tree}")
                                             and worktree_clean()))
        if build["build_status"] == "unchanged":
            state.update(status="unchanged", release=previous)
            save_state(state)
            return
        report = json.loads(REPORT.read_text())
        manifest = (build_site.OUTPUT / MANIFEST).read_bytes()
        groups = decode_manifest(manifest)
        for result in report["groups"]:
            slug = result["slug"]
            if result["publication_status"] == "updated" and groups[slug] == previous_groups.get(slug):
                result["publication_status"] = "unchanged"
        REPORT.write_bytes(encode(report))
        release = validate_release({
            "version": 1, "run_id": os.environ["GITHUB_RUN_ID"],
            "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "source_commit": commit,
            "source_tree": git_revision("HEAD^{tree}"),
            "site_sha256": site_digest(build_site.OUTPUT), "manifest_sha256": digest(manifest),
        })
        unchanged = previous and all(release[key] == previous[key] for key in
                                     ("source_tree", "site_sha256", "manifest_sha256"))
        state.update(status="unchanged" if unchanged else "prepared", release=release)
        if not unchanged:
            (build_site.OUTPUT / RELEASE).write_bytes(encode(release))
            build_site.enforce_site_budget(build_site.OUTPUT)
            output("deploy", "true")
        save_state(state)
    except Exception as error:
        state.update(status="failed", error=str(error))
        save_state(state)
        raise


def record_candidate(records):
    state = json.loads(STATE.read_text())
    if state["status"] != "prepared":
        raise ValueError("Only a prepared publication may be recorded")
    release = validate_release(state["release"])
    if records.main_commit() != release["source_commit"]:
        state["status"] = "superseded"
        save_state(state)
        output("deploy", "false")
        return
    if (site_digest(build_site.OUTPUT) != release["site_sha256"] or
            json.loads((build_site.OUTPUT / RELEASE).read_bytes()) != release):
        raise ValueError("Candidate changed after preparation")
    identifier = records.create(release)
    state.update(status="awaiting_deployment", deployment_id=identifier)
    save_state(state)
    records.status(identifier, "in_progress", run_url(release))
    output("deploy", "true")


def restore_candidate(records, archive):
    state = json.loads(STATE.read_text())
    latest = records.latest()
    if (state["status"] != "retrying" or not latest or latest["id"] != state["deployment_id"]
            or latest["payload"] != state["release"]):
        raise RecoveryError("Retry must use the latest recorded candidate")
    release = validate_release(state["release"])
    if build_site.OUTPUT.exists():
        raise ValueError("Retry needs a clean output directory")
    with tempfile.TemporaryDirectory(prefix=".publication-retry-", dir=ROOT) as temporary:
        folder = Path(temporary)
        with tarfile.open(archive, "r:") as source:
            size = 0
            members = set()
            for index, member in enumerate(source):
                if index >= 20000:
                    raise ValueError("Candidate archive contains too many entries")
                name = member.name.removeprefix("./").rstrip("/")
                if name in {"", "."} and member.isdir():
                    continue
                if (not name or len(name) > 512 or "\\" in name
                        or any(part in {"", ".", ".."} for part in name.split("/"))
                        or name in members or not (member.isfile() or member.isdir())):
                    raise ValueError("Candidate archive has unsafe paths or file types")
                members.add(name)
                size += member.size
                if size > 1024 * 1024 * 1024:
                    raise ValueError("Candidate archive exceeds 1 GiB")
                target = folder / name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as content, target.open("wb") as destination:
                        while chunk := content.read(1024 * 1024):
                            destination.write(chunk)
        if (json.loads((folder / RELEASE).read_bytes()) != release
                or digest((folder / MANIFEST).read_bytes()) != release["manifest_sha256"]
                or site_digest(folder) != release["site_sha256"]):
            raise RecoveryError("Saved artifact does not match the recorded candidate")
        decode_manifest((folder / MANIFEST).read_bytes())
        build_site.enforce_site_budget(folder)
        folder.rename(build_site.OUTPUT)
    state["status"] = "awaiting_deployment"
    save_state(state)
    records.status(state["deployment_id"], "in_progress", run_url(release))
    output("deploy", "true")


def verify(records):
    state = json.loads(STATE.read_text())
    try:
        latest = records.latest()
        if not latest or latest["id"] != state["deployment_id"] or latest["payload"] != state["release"]:
            raise RecoveryError("Publication record changed; stop concurrent publishers")
        served_release(state["url"], state["release"])
        records.status(state["deployment_id"], "success", run_url(state["release"]))
        state["status"] = "verified"
    except Exception as error:
        state.update(status="unverified", error=str(error))
        raise
    finally:
        save_state(state)


def summary():
    state = json.loads(STATE.read_text()) if STATE.exists() else {"status": "not prepared"}
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {"groups": []}
    def cell(value):
        escaped = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
        for character in "|[]`*_\\":
            escaped = escaped.replace(character, f"&#{ord(character)};")
        return escaped
    lines = ["## Course publication", "", "Result: **" + cell(state["status"]) + "**.", "",
             "Group statuses describe selected content; only a verified publication confirms deployment.", "",
             "| Group | Status | Selected revision | Attempted revision | Detail |",
             "| --- | --- | --- | --- | --- |"]
    for result in report["groups"]:
        selected = result.get("selected_source") or {}
        values = (result["slug"], result.get("publication_status", result["status"]),
                  selected.get("commit", "—"), result.get("commit") or "—", result.get("error", ""))
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    if report.get("transport"):
        lines.extend(["", "Build transport (deployment and issue APIs excluded):", "",
                      "| Measurement | Value |", "| --- | --- |"])
        lines.extend("| " + cell(key) + " | " + cell(value) + " |"
                     for key, value in report["transport"].items())
    if state.get("error"):
        lines.extend(["", cell(state["error"])])
    if state.get("deployment_id"):
        lines.extend(["", "Publication record: " + str(state["deployment_id"])])
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "record", "restore", "verify", "summary"))
    args = parser.parse_args()
    if args.command == "summary":
        summary()
    else:
        records = DeploymentRecords(os.environ["GITHUB_REPOSITORY"])
        if args.command == "prepare":
            prepare(records, os.environ["PAGES_URL"], os.environ.get("FIRST_PUBLICATION") == "true",
                    os.environ.get("RETRY_PUBLICATION") == "true")
        elif args.command == "record":
            record_candidate(records)
        elif args.command == "restore":
            restore_candidate(records, ROOT / ".scratch/pending-artifact/artifact.tar")
        else:
            verify(records)
