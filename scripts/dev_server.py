"""Serve current frontend sources with the existing local preview's entries."""

import argparse
import shutil
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from build_site import ROOT, OUTPUT, main as build_site, write_editor


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT), **kwargs)

    def translate_path(self, path):
        built = Path(super().translate_path(path))
        relative = built.relative_to(OUTPUT)
        if not relative.parts:
            return str(ROOT / "index.html")
        # Read frontend edits directly; generated entry snapshots stay in _site.
        if relative.parts[0] in {"index.html", "static", "editor", "materialities-of-ai"}:
            source = ROOT / relative
            if source.is_file():
                return str(source)
            if source.is_dir() and (source / "index.html").is_file():
                return str(source)
        return str(built)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not (OUTPUT / "entries.json").is_file():
        build_site()
    # Refresh generated editor files without replacing saved group snapshots.
    write_editor_target = OUTPUT / "editor"
    # write_editor copies into a new directory; generate in a temporary sibling.
    with tempfile.TemporaryDirectory(prefix="ddd-dev-") as temporary:
        candidate = Path(temporary)
        write_editor(candidate)
        shutil.copytree(candidate / "editor", write_editor_target, dirs_exist_ok=True)
    ThreadingHTTPServer.request_queue_size = 128
    with ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler) as server:
        print(f"Preview: http://localhost:{args.port}/", flush=True)
        print("Refresh the browser after editing. Stop with Ctrl+C.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
