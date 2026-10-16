"""Generate checked-in course copies from the canonical templates/group files.

Run without arguments to check drift; use --write after editing the canonical
student guide or demonstration. The root README introduction/layout stay local.
"""

import argparse
from pathlib import Path
import re

from content_contract import select_content_files


ROOT = Path(__file__).resolve().parent.parent
START = '<!-- BEGIN GENERATED STUDENT GUIDE: templates/group/README.md -->'
END = '<!-- END GENERATED STUDENT GUIDE -->'


def expected_copies(root):
    root = Path(root)
    template = root / 'templates/group'
    guide = (template / 'README.md').read_text(encoding='utf-8')
    heading = '## 1. Prerequisites\n'
    if guide.count(heading) != 1:
        raise ValueError('Canonical guide must contain exactly one prerequisites heading')
    guide = heading + guide.split(heading, 1)[1]
    readme = (root / 'README.md').read_text(encoding='utf-8')
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError('Root README must contain exactly one generated guide block')
    before, remainder = readme.split(START)
    _, after = remainder.split(END)
    expected = {Path('README.md'): (before + START + '\n\n' + guide.rstrip()
                                  + '\n\n' + END + after).encode('utf-8')}
    sizes = {path.relative_to(template).as_posix(): path.stat().st_size
             for path in template.rglob('*') if path.is_file()}
    for name in select_content_files(sizes):
        payload = (template / name).read_bytes()
        if name == 'text.md':
            # Only these display labels differ; all demonstration prose is shared.
            text = payload.decode('utf-8')
            front, body = text.removeprefix('---\n').split('\n---\n', 1)
            for key, value in {'title': 'Example: Three Values', 'authors': 'Course team'}.items():
                front, count = re.subn(rf'^{key}:.*$', f'{key}: {value}', front, flags=re.M)
                if count != 1:
                    raise ValueError(f'Canonical demonstration needs one {key}')
            payload = ('---\n' + front + '\n---\n' + body).encode('utf-8')
        expected[Path('example') / name] = payload
    return expected


def sync(root=ROOT, *, write=False):
    root = Path(root)
    expected = expected_copies(root)
    # An obsolete demo asset needs deliberate removal, rather than being left
    # silently published or deleted by a general-purpose synchronization command.
    extras = sorted(path.relative_to(root) for path in (root / 'example').rglob('*')
                    if path.is_file() and path.relative_to(root) not in expected)
    if extras:
        raise ValueError('Unexpected example files; review/remove explicitly: '
                         + ', '.join(map(str, extras)))
    stale = []
    for name, payload in expected.items():
        path = root / name
        if path.is_symlink():
            raise ValueError(f'Refusing generated symlink: {name}')
        if not path.is_file() or path.read_bytes() != payload:
            stale.append(name)
    if write:
        for name in stale:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected[name])
    return stale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='regenerate stale copies')
    args = parser.parse_args()
    try:
        stale = sync(write=args.write)
    except ValueError as error:
        parser.exit(1, f'{error}\n')
    if stale and not args.write:
        parser.exit(1, 'Course material drift: ' + ', '.join(map(str, stale))
                    + '\nEdit templates/group/, then run python3 scripts/sync_course_materials.py --write\n')
    print(('Updated: ' + ', '.join(map(str, stale))) if stale else 'Course material copies are current.')


if __name__ == '__main__':
    main()
