"""Adapt the explicitly connected HTML course brief to the course content contract.

Only content inside article#abstract is read. Remote scripts, styles and event
attributes are never published. The importer verifies source blobs first.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import hashlib
import json
import re
from urllib.parse import urlsplit

from content_contract import CONTRACT
from github_content import ImportFailure


REPOSITORY = 'ginevraterenghi/multimodal-abstract-ddd'
COLORS = {'red': '#FF09D6', 'blue': '#0EB5BE', 'yellow': '#E5B800',
          'purple': '#8800FF', 'green': '#00E279'}


@dataclass
class Element:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element('document', {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'img', 'br', 'hr', 'meta', 'link', 'input', 'source'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ValueError(f'HTML brief: mismatched closing tag {tag}')
        self.stack.pop()

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def walk(node):
    if isinstance(node, Element):
        yield node
        for child in node.children:
            yield from walk(child)


def source_url(value):
    if (not isinstance(value, str) or re.search(r'[\s<>"\\\x00-\x1f]', value)
            or urlsplit(value).scheme not in {'http', 'https'}):
        raise ValueError('HTML brief: source links must be HTTP(S) URLs')
    return value


def convert(html, sources, stylesheet=''):
    document = Document()
    document.feed(html)
    document.close()
    articles = [node for node in walk(document.root)
                if node.tag == 'article' and node.attrs.get('id') == 'abstract']
    if len(articles) != 1:
        raise ValueError('HTML brief: expected exactly one article#abstract')
    sources = json.loads(sources)
    if not isinstance(sources, dict):
        raise ValueError('HTML brief: expected a source URL map')
    colors = dict(COLORS)
    for name, value in re.findall(r'--(red|blue|yellow|purple|green)\s*:\s*(#[0-9a-fA-F]{6})\s*;', stylesheet):
        colors[name] = value

    def render(node, linked=False):
        if isinstance(node, str):
            text = re.sub(r'\s+', ' ', node)
            return text.replace('[', r'\[').replace(']', r'\]')
        tag, attrs = node.tag, node.attrs
        if tag in {'script', 'style', 'iframe', 'object'}:
            raise ValueError(f'HTML brief: unsupported content element {tag}')
        if tag in {'img', 'video', 'audio'}:
            path = attrs.get('src', '')
            if not re.fullmatch(r'media/[a-z0-9][a-z0-9._-]*', path):
                raise ValueError('HTML brief: media must use a local media/ path')
            number = path.rsplit('/', 1)[-1].split('.')[0]
            url = sources.get('media-' + number) if not linked else None
            source = source_url(url) if url else None
            if tag == 'img':
                alt = attrs.get('alt', f'Media {number}').replace('[', '').replace(']', '')
                image = f'![{alt}]({path})'
                return f'[{image}]({source})' if source else image
            label = 'Video' if tag == 'video' else 'Audio'
            modifier = f'|source={source}' if source else ''
            return f'[[{label} {number}|{path}{modifier}]]'
        body = ''.join(render(child, linked or tag == 'a') for child in node.children)
        if tag == 'a':
            url = source_url(attrs.get('href', ''))
            if any(child.tag in {'video', 'audio'} for child in walk(node)):
                return body.strip().replace(']]', f'|source={url}]]')
            return f'[{body.strip()}]({url})'
        if tag in {'strong', 'em', 'u'}:
            if tag == 'u':
                return '{underline|' + body.strip() + '}'
            if tag == 'em':
                body = re.sub(r'\{✶\s*(.*?)\}', r'\1', body)
            marker = '**' if tag == 'strong' else '*'
            return marker + body.strip() + marker
        if tag == 'span':
            classes = attrs.get('class', '').split()
            color = next((colors[name] for name in classes if name in colors), None)
            if color:
                # Separate a trailing research question from the bold heading
                # so adjacent emphasis delimiters cannot merge in Markdown.
                question = re.search(r'\*([^*]+)\*$', body)
                if question:
                    parts = (body[:question.start()].strip(), '*' + question[1] + '*',
                             body[question.end():].strip())
                    return ' '.join('{' + color + '|' + part + '}' for part in parts if part)
                return '{' + color + '|' + body.strip() + '}'
            if 'reference' in classes:
                citation = re.fullmatch(r'\(■\s*(.+?)\.\s+(.+)\)', body.strip())
                if citation:
                    return f'[[{citation[1]}|{citation[2]}|type=bibliography]]'
                raise ValueError('HTML brief: expected a citation (■ Author, year. Title)')
        if tag in {'article', 'span', 'p'}:
            return body
        if tag == 'br':
            return ' '
        raise ValueError(f'HTML brief: unsupported content element {tag}')

    body = re.sub(r'\s+', ' ', render(articles[0])).strip()
    if not body:
        raise ValueError('HTML brief: empty article')
    return ('---\ntitle: Materialities of AI\n---\n\n' + body + '\n').encode('utf-8')


def handles_repository(repository):
    """One allowlisted source; ordinary HTML repositories never opt in."""
    return repository.casefold() == REPOSITORY.casefold()


def prepare(group, tree, client, commit, *, validate_tree):
    """Return a contract-shaped listing and generated bytes, or pass through.

    All source selection, byte bounds and exact-commit fetching live here. The
    importer supplies its normal tree validator before any source download and
    validates the resulting content again afterward. Native text.md wins.
    """
    if not handles_repository(group.repository) or any(
            node.get('path') == 'text.md' for node in tree.get('tree', [])):
        return tree, {}
    generated = {}
    # Validate the entire listing before fetching source HTML. A synthetic
    # text node lets the normal contract validate all the original media.
    generated_node = {'path': 'text.md', 'mode': '100644', 'type': 'blob',
                      'size': 0, 'sha': hashlib.sha1(b'blob 0\0').hexdigest()}
    tree = {**tree, 'tree': [*tree['tree'], generated_node]}
    validate_tree(tree, group.exclude, require_visual=group.require_visual)
    source = {}
    for name in ('index.html', 'sources.json', 'style.css'):
        node = next((item for item in tree['tree'] if item['path'] == name), None)
        if node is None or type(node.get('size')) is not int or not 0 < node['size'] <= CONTRACT['limits']['text_bytes']:
            raise ValueError(f'HTML brief: missing or oversized {name}')
        data = client.raw_file(group.repository, commit, name, node['size'])
        if (len(data) != node['size'] or hashlib.sha1(
                f'blob {len(data)}\0'.encode() + data).hexdigest() != node['sha']):
            raise ImportFailure('invalid_blob', f'{name}: bytes differ from the source commit')
        source[name] = data.decode('utf-8')
    generated['text.md'] = convert(source['index.html'], source['sources.json'], source['style.css'])
    generated_node.update(size=len(generated['text.md']), sha=hashlib.sha1(
        f"blob {len(generated['text.md'])}\0".encode() + generated['text.md']).hexdigest())
    return tree, generated
