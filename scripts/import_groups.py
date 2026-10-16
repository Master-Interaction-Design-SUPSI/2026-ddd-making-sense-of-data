"""Import content from exact Git objects into fresh, isolated group folders."""

import hashlib
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from content_contract import CONTRACT, MEDIA_TABLE, select_content_files, validate_visual
from github_content import GitHubClient, ImportFailure


LINK_CHECKER = Path(__file__).with_name("check_content_links.js")
MAX_WARNINGS = 20  # Keeps every group's report entry small; the page shows each missing item.
MAX_TREE_ENTRIES = 2000
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def object_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ImportFailure("invalid_response", "GitHub returned an invalid object identifier")
    return value


def validate_tree(tree, exclude=()):
    nodes = tree.get("tree")
    if tree.get("truncated") is not False or not isinstance(nodes, list) or len(nodes) > MAX_TREE_ENTRIES:
        raise ImportFailure("tree_limit", "Repository listing is incomplete or exceeds 2,000 entries; reduce repository contents")
    files = {}
    paths = set()
    for node in nodes:
        name = node["path"]
        if not isinstance(name, str) or len(name) > 512 or len(name.split("/")) > 16:
            raise ValueError("Repository paths must be at most 512 characters and 16 components")
        if "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name) or any(p in {"", ".", ".."} for p in name.split("/")):
            raise ValueError(f"Unsafe repository path: {name!r}")
        if name in paths:
            raise ValueError(f"Duplicate repository path: {name!r}")
        paths.add(name)
        if node["type"] == "tree" and node["mode"] == "040000":
            continue
        if node["type"] != "blob" or node["mode"] not in {"100644", "100755"}:
            raise ValueError(f"{name}: symlinks, submodules and non-regular files are not supported")
        object_sha(node["sha"])
        files[name] = node
    for name in paths:
        if any("/".join(name.split("/")[:n]) in files for n in range(1, len(name.split("/")))):
            raise ValueError(f"{name}: a parent path is a file")
    selected = select_content_files({name: node["size"] for name, node in files.items() if name not in exclude})
    return {name: files[name] for name in selected}


def validate_svg_content(path):
    source = path.read_text(encoding="utf-8")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", source, re.I):
        raise ValueError(f"{path.name}: SVG document types and entities are not supported")
    if re.search(r"<\?(?!xml\s)", source, re.I):
        raise ValueError(f"{path.name}: SVG processing instructions are not supported")
    try:
        svg = ET.fromstring(source)
    except ET.ParseError as error:
        raise ValueError(f"{path.name}: export a valid SVG file ({error})") from error
    if svg.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise ValueError(f"{path.name}: expected an SVG image")
    blocked = {"script", "foreignobject", "a", "animate", "animatemotion", "animatetransform", "set", "discard"}
    for element in svg.iter():
        tag = element.tag.split("}")[-1].lower()
        if not element.tag.startswith(f"{{{SVG_NAMESPACE}}}") or tag in blocked:
            raise ValueError(f"{path.name}: use a static SVG without embedded code, links or animation")
        for name, value in element.attrib.items():
            attribute = name.split("}")[-1].lower()
            if attribute.startswith("on"):
                raise ValueError(f"{path.name}: SVG event handlers are not supported")
            if attribute == "href" and not (value.startswith("#") or re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+", value)):
                raise ValueError(f"{path.name}: embed image resources; external SVG references are not supported")
            if attribute == "style" or "url(" in value.lower():
                validate_svg_style(value, path.name)
        if tag == "style":
            validate_svg_style("".join(element.itertext()), path.name)


def validate_svg_style(value, name):
    # Static exported artwork may use local paint servers, not imported CSS/resources.
    references = re.findall(r"url\s*\(([^)]*)\)", value, re.I)
    if "\\" in value or "@" in value or "/*" in value or any(not re.fullmatch(r"#[\w.-]+", ref.strip().strip("\"'")) for ref in references):
        raise ValueError(f"{name}: SVG styles may reference only local # identifiers")


def validate_references(text, files, table=None):
    """Reject unsafe targets; return warnings for names and files that do not resolve."""
    payload = json.dumps({"text": text, "files": list(files), "schemes": CONTRACT["reference_schemes"],
                          "table": table})
    try:
        checked = subprocess.run(["node", str(LINK_CHECKER), "--validate"], input=payload, text=True,
                                 capture_output=True, timeout=10, check=True)
    except subprocess.TimeoutExpired as error:
        raise ValueError("text.md: link validation exceeded 10 seconds; simplify the text") from error
    except subprocess.CalledProcessError as error:
        if error.returncode == 2 and error.stderr:
            # Messages naming their file keep it; others concern text.md.
            message = error.stderr.strip()
            named = message.startswith(("text.md:", MEDIA_TABLE))
            raise ValueError(message if named else "text.md: " + message) from error
        raise RuntimeError("Text validator failed; check the local Node/Markdown setup") from error
    warnings = [message[:300] for message in json.loads(checked.stdout)["warnings"]]
    if len(warnings) > MAX_WARNINGS:
        warnings[MAX_WARNINGS:] = [f"text.md: {len(warnings) - MAX_WARNINGS} more references do not resolve"]
    return warnings


def validate_content(folder, files):
    """Reject unsafe or malformed content; return warnings about references that do not resolve."""
    for name in files:
        if name.endswith(".svg"):
            validate_svg_content(folder / name)
    text = (folder / "text.md").read_text(encoding="utf-8")
    if "visual.svg" in files:
        validate_visual(folder / "visual.svg")
    table = None
    if MEDIA_TABLE in files:
        try:
            table = (folder / MEDIA_TABLE).read_bytes().decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"{MEDIA_TABLE}: save the table as a UTF-8 CSV file") from error
    return validate_references(text, files, table)


def import_group(group, destination, client, result, *, previous=None, allow_unchanged=False):
    repository = "/repos/" + quote(group.repository, safe="/")
    metadata = client.json(repository)
    if metadata.get("private") is not False or metadata.get("visibility") != "public":
        raise ImportFailure("non_public", "Only public group repositories can be imported")
    if metadata.get("full_name", "").casefold() != group.repository.casefold():
        raise ImportFailure("redirect", "Repository identity changed; update groups.json")
    reference = client.json(repository + "/git/ref/heads/" + quote(group.branch, safe="/"), missing="missing_branch")
    if reference["ref"] != "refs/heads/" + group.branch or reference["object"]["type"] != "commit":
        raise ImportFailure("missing_branch", "GitHub did not return the exact configured branch")
    commit = object_sha(reference["object"]["sha"])
    result["commit"] = commit
    base = previous.groups.get(group.slug) if previous else None
    same_identity = (base and base["repository"] == group.repository and base["branch"] == group.branch)
    problem = previous.problem(group) if previous else None
    if allow_unchanged and problem and problem["commit"] == commit:
        # Already rejected with this code; checking it again would only repeat the reason.
        result.update(status="rejected", error_code="invalid_content", error=problem["message"])
        return
    commit_data = client.json(repository + "/git/commits/" + commit)
    if commit_data["sha"] != commit:
        raise ImportFailure("invalid_response", "GitHub returned a different commit")
    committed_at = commit_data["committer"]["date"]
    if not isinstance(committed_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", committed_at):
        raise ImportFailure("invalid_response", "GitHub returned an invalid commit date")
    result["committed_at"] = committed_at
    tree_sha = object_sha(commit_data["tree"]["sha"])
    result['tree'] = tree_sha
    tree = client.json(repository + "/git/trees/" + tree_sha + "?recursive=1")
    if tree["sha"] != tree_sha:
        raise ImportFailure("invalid_response", "GitHub returned a different tree")
    files = validate_tree(tree, group.exclude)
    result["excluded_files"] = sorted(node["path"] for node in tree["tree"]
                                      if node["path"] in group.exclude)
    result['excluded_blobs'] = {name: next((node.get('sha') for node in tree['tree']
        if node['path'] == name and node.get('type') == 'blob'
        and isinstance(node.get('sha'), str) and re.fullmatch(r'[0-9a-f]{40}', node['sha'])), None)
        for name in group.exclude}
    if allow_unchanged and same_identity and base["commit"] == commit and previous.can_skip(group, files):
        result.update(status="unchanged", files=list(files))
        return
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=destination) as temporary:
        candidate = Path(temporary)
        for name, node in files.items():
            data = previous.reuse(group.slug, name, node) if same_identity else None
            if data is not None:
                result["reused_bytes"] = result.get("reused_bytes", 0) + len(data)
            else:
                data = client.raw_file(group.repository, commit, name, node["size"])
            digest = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()
            if len(data) != node["size"] or digest != node["sha"]:
                raise ImportFailure("invalid_blob", f"{name}: downloaded bytes do not match the source commit")
            if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError(f"{name}: Git LFS pointers are not supported; upload the actual file")
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        result["warnings"] = validate_content(candidate, files)
        client.remaining()
        candidate.rename(destination / group.slug)
    result.update(status="imported", files=list(files))


def import_groups(groups, destination, client_factory=GitHubClient, *, previous=None, allow_unchanged=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    deadline = time.monotonic() + 15 * 60
    api_budget = {"requests": 0, "maximum": 100, "remaining": None}
    for group in groups:
        live = group.state == "live"
        result = dict(slug=group.slug, repository=group.repository, branch=group.branch,
                      commit=None, status="failed" if live else "disabled")
        results.append(result)
        if not live:
            continue
        try:
            target = destination / group.slug
            if target.exists() or target.is_symlink():
                raise ValueError(f"{group.slug}: staging destination already exists; use a fresh directory")
            started = time.monotonic()
            client = client_factory()
            client.api_budget = api_budget
            client.deadline = min(client.deadline, deadline)
            if previous:
                previous.deadline = client.deadline
            try:
                import_group(group, destination, client, result, previous=previous, allow_unchanged=allow_unchanged)
            finally:
                result["transport"] = {**client.metrics, "elapsed_seconds": round(time.monotonic() - started, 3)}
        except ImportFailure as error:
            result.update(error_code=error.code, error=str(error))
        except (ValueError, ET.ParseError) as error:
            result.update(error_code="invalid_content", error=str(error))
        except (KeyError, TypeError, AttributeError):
            result.update(error_code="invalid_response", error="GitHub returned malformed repository metadata")
    return results
