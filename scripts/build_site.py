"""Prepare the static site and discover entries from folders at the repo root."""

import json
import shutil
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "_site"
VISUAL_NAMES = ("visual.png", "visual.jpg", "visual.jpeg", "visual.webp", "visual.gif")


def find_entries():
    entries = []
    for folder in ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in {"static", "scripts", "_site"}:
            continue
        has_text = (folder / "text.md").is_file()
        visuals = [name for name in VISUAL_NAMES if (folder / name).is_file()]
        if not has_text and not visuals:
            continue
        if not has_text or not visuals:
            warnings.warn(f"{folder.name}: waiting for text.md and a visual.* file")
            continue
        if len(visuals) != 1:
            raise ValueError(f"{folder.name}: expected exactly one visual.* file")
        entries.append(folder.name)
    return sorted(entries, key=lambda name: (name != "example", name.casefold()))


def main():
    entries = find_entries()
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()
    shutil.copy2(ROOT / "index.html", OUTPUT / "index.html")
    shutil.copytree(ROOT / "static", OUTPUT / "static")
    for name in entries:
        shutil.copytree(ROOT / name, OUTPUT / name)
    (OUTPUT / "entries.json").write_text(json.dumps(entries, ensure_ascii=False) + "\n")
    print(f"Prepared {len(entries)} entries: {', '.join(entries)}")


if __name__ == "__main__":
    main()
