"""Import content from exact Git objects into fresh, isolated group folders."""

import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from content_contract import select_content_files, validate_visual
from github_content import GitHubClient, ImportFailure


LINK_CHECKER = Path(__file__).with_name("check_content_links.js")
MAX_TREE_ENTRIES = 2000
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def object_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ImportFailure("invalid_response", "GitHub returned an invalid object identifier")
    return value


def validate_tree(tree, exclude=(), *, require_visual=True):
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
    selected = select_content_files({name: node["size"] for name, node in files.items() if name not in exclude},
                                    require_visual=require_visual)
    return {name: files[name] for name in selected}


def validate_svg_content(path):
    source = path.read_text(encoding="utf-8")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", source, re.I):
        raise ValueError(f"{path.name}: SVG document types and entities are not supported")
    if re.search(r"<\?(?!xml\s)", source, re.I):
        raise ValueError(f"{path.name}: SVG processing instructions are not supported")
    svg = ET.fromstring(source)
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


def validate_references(text, files):
    try:
        checked = subprocess.run(["node", str(LINK_CHECKER)], input=text, text=True,
                                 capture_output=True, timeout=10, check=True)
    except subprocess.TimeoutExpired as error:
        raise ValueError("text.md: link validation exceeded 10 seconds; simplify the text") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Text validator failed; check the local Node/Markdown setup") from error
    try:
        references = json.loads(checked.stdout)
    except ValueError as error:
        raise RuntimeError("Text validator returned invalid JSON") from error
    if not isinstance(references, list):
        raise RuntimeError("Text validator did not return a reference list")
    for target in references:
        if not isinstance(target, str) or any(ord(c) < 32 or ord(c) == 127 for c in target) or "\\" in target:
            raise ValueError("text.md: invalid link or media reference")
        parsed = urlsplit(target)
        if parsed.scheme:
            if parsed.scheme.lower() not in {"https", "http", "mailto"}:
                raise ValueError(f"text.md: unsupported URL scheme in {target!r}")
            continue
        if target.startswith("#"):
            continue
        name = unquote(parsed.path)
        if name.startswith("./"):
            name = name[2:]
        if parsed.netloc or name.startswith("/") or "\\" in name or any(p in {"", ".", ".."} for p in name.split("/")) or name not in files:
            raise ValueError(f"text.md: {target!r} must reference an imported file inside this group")


def validate_content(folder, files, *, require_visual=True):
    for name in files:
        if name.endswith(".svg"):
            validate_svg_content(folder / name)
    text = (folder / "text.md").read_text(encoding="utf-8")
    if require_visual or "visual.svg" in files:
        validate_visual(folder / "visual.svg")
    validate_references(text, files)


def import_group(group, destination, client, result):
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
    commit_data = client.json(repository + "/git/commits/" + commit)
    if commit_data["sha"] != commit:
        raise ImportFailure("invalid_response", "GitHub returned a different commit")
    tree_sha = object_sha(commit_data["tree"]["sha"])
    tree = client.json(repository + "/git/trees/" + tree_sha + "?recursive=1")
    if tree["sha"] != tree_sha:
        raise ImportFailure("invalid_response", "GitHub returned a different tree")
    files = validate_tree(tree, group.exclude, require_visual=group.require_visual)
    result["excluded_files"] = sorted(node["path"] for node in tree["tree"]
                                      if node["path"] in group.exclude)
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=destination) as temporary:
        candidate = Path(temporary)
        for name, node in files.items():
            data = client.get(repository + "/git/blobs/" + node["sha"], node["size"], raw=True)
            digest = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()
            if len(data) != node["size"] or digest != node["sha"]:
                raise ImportFailure("invalid_blob", f"{name}: downloaded bytes do not match the source commit")
            if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError(f"{name}: Git LFS pointers are not supported; upload the actual file")
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        validate_content(candidate, files, require_visual=group.require_visual)
        client.remaining()
        candidate.rename(destination / group.slug)
    result.update(status="imported", files=list(files))


def import_groups(groups, destination, client_factory=GitHubClient):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for group in groups:
        result = dict(slug=group.slug, repository=group.repository, branch=group.branch,
                      commit=None, status="disabled" if not group.enabled else "failed")
        if not group.require_visual:
            result["require_visual"] = False
        results.append(result)
        if not group.enabled:
            continue
        try:
            target = destination / group.slug
            if target.exists() or target.is_symlink():
                raise ValueError(f"{group.slug}: staging destination already exists; use a fresh directory")
            import_group(group, destination, client_factory(), result)
        except ImportFailure as error:
            result.update(error_code=error.code, error=str(error))
        except (ValueError, ET.ParseError) as error:
            result.update(error_code="invalid_content", error=str(error))
        except (KeyError, TypeError, AttributeError):
            result.update(error_code="invalid_response", error="GitHub returned malformed repository metadata")
    return results
