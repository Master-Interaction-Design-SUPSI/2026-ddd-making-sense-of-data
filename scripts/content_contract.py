"""Select publishable paths and enforce per-group byte/count limits.

The importer must supply regular-file sizes from a single source commit and
reject symlinks/submodules before using this manifest validator.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


CONTRACT = json.loads((Path(__file__).resolve().parent.parent / "static" / "contract.json").read_text())
CONTRACT_VERSION = CONTRACT["version"]
LIMITS = CONTRACT["limits"]
MEDIA_EXTENSIONS = frozenset(CONTRACT["media_extensions"])
MAX_TEXT_BYTES = LIMITS["text_bytes"]
MEDIA_TABLE = CONTRACT["media_table"]
MAX_TABLE_BYTES = LIMITS["table_bytes"]
MAX_SVG_BYTES = LIMITS["svg_bytes"]
MAX_MEDIA_BYTES = LIMITS["media_bytes"]
MAX_TOTAL_BYTES = LIMITS["group_bytes"]
MAX_SITE_BYTES = LIMITS["site_bytes"]
MAX_FILES = LIMITS["files"]
REQUIRED_FILES = tuple(CONTRACT["required_files"])
PATH_PART = re.compile(CONTRACT["path_component"])


def validate_visual(path):
    try:
        svg = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ValueError(f"{path}: export a valid SVG file; renaming an image is not enough") from error
    if svg.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError(f"{path}: expected an SVG document")
    try:
        viewbox = [float(value) for value in re.split(r"[\s,]+", svg.get("viewBox", "").strip())]
    except ValueError:
        viewbox = []
    if viewbox != CONTRACT["visual_viewbox"]:
        raise ValueError(f'{path}: export a 1920 × 1080 canvas (viewBox="0 0 1920 1080")')
    for attribute, expected in (("width", 1920), ("height", 1080)):
        value = svg.get(attribute)
        if value is None:
            continue
        try:
            size = float(value.strip().removesuffix("px"))
        except ValueError:
            size = None
        if size != expected:
            raise ValueError(f"{path}: {attribute} must be {expected} pixels or omitted")


def select_content_files(file_sizes, *, required=REQUIRED_FILES):
    """Return sorted allowed paths; ignore non-content files, reject bad content."""
    selected = []
    total = 0
    for name, size in file_sizes.items():
        if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("content path: expected a relative POSIX file path")
        parts = name.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"{name}: absolute paths and traversal are not allowed")
        path = PurePosixPath(name)
        if len(parts) == 1 and path.stem.lower() == "visual" and name != "visual.svg":
            raise ValueError(f"{name}: use exactly visual.svg for the main visual")
        root_file = name in {"text.md", "visual.svg"}
        media = path.suffix.lower() in MEDIA_EXTENSIONS and (len(parts) == 1 or parts[0] == "media")
        # Selected whenever present; import admission is where media.csv becomes mandatory.
        table = name == MEDIA_TABLE
        if not root_file and not media and not table:
            continue
        if len(name) > LIMITS["path_length"] or len(parts) > LIMITS["path_depth"]:
            raise ValueError(f"{name}: content path too long")
        if not all(PATH_PART.fullmatch(part) for part in parts):
            raise ValueError(f"{name}: content filenames must use lowercase letters, digits, dots, hyphens or underscores")
        if type(size) is not int or size < 0:
            raise ValueError(f"{name}: invalid file size")
        limit = (MAX_TEXT_BYTES if name == "text.md" else MAX_TABLE_BYTES if name == MEDIA_TABLE
                 else MAX_SVG_BYTES if path.suffix == ".svg" else MAX_MEDIA_BYTES)
        if size > limit:
            raise ValueError(f"{name}: exceeds the {limit}-byte file limit")
        selected.append(name)
        total += size
        if len(selected) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError(f"group content exceeds {MAX_FILES} files or {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")
    if not set(required) <= set(selected):
        raise ValueError("group content requires " + " and ".join(sorted(required)))
    return sorted(selected)
