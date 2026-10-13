"""Select publishable paths and enforce per-group byte/count limits.

The importer must supply regular-file sizes from a single source commit and
reject symlinks/submodules before using this manifest validator.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath


MEDIA_EXTENSIONS = frozenset({
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif",
    ".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac",
    ".mp4", ".webm", ".mov",
})
MAX_TEXT_BYTES = 256 * 1024
MAX_SVG_BYTES = 5 * 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILES = 100
PATH_PART = re.compile(r"[a-z0-9][a-z0-9._-]*")


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
    if viewbox != [0, 0, 1920, 1080]:
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


def select_content_files(file_sizes):
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
        required = name in {"text.md", "visual.svg"}
        media = path.suffix.lower() in MEDIA_EXTENSIONS and (len(parts) == 1 or parts[0] == "media")
        if not required and not media:
            continue
        if not all(PATH_PART.fullmatch(part) for part in parts):
            raise ValueError(f"{name}: content filenames must use lowercase letters, digits, dots, hyphens or underscores")
        if type(size) is not int or size < 0:
            raise ValueError(f"{name}: invalid file size")
        limit = MAX_TEXT_BYTES if name == "text.md" else MAX_SVG_BYTES if path.suffix == ".svg" else MAX_MEDIA_BYTES
        if size > limit:
            raise ValueError(f"{name}: exceeds the {limit}-byte file limit")
        selected.append(name)
        total += size
        if len(selected) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("group content exceeds 100 files or 100 MiB")
    if not {"text.md", "visual.svg"} <= set(selected):
        raise ValueError("group content requires text.md and visual.svg")
    return sorted(selected)
