"""Bounded request admission and truthful, idempotent publication feedback."""

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from group_registry import REPOSITORY, SLUG
from publication_state import RecoveryError
from github_content import GitHubClient

LABEL = 'publication-request'
MARKER = '<!-- ddd-publication-request:v1 -->'
MAX_BODY = 8192
MAX_BATCH = 6
MAX_PAGES = 3
PAGE_SIZE = 100
TRANSIENT = frozenset({'timeout', 'rate_limited', 'remote_error', 'network_error',
                       'unavailable_repository', 'access_denied', 'redirect', 'invalid_response'})


class RequestAPI:
    """One fixed course repository, with separate bounded issue-only authority."""

    def __init__(self, repository, client=None):
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
            raise ValueError('invalid course repository')
        self.base = '/repos/' + repository
        self.client = client or GitHubClient()
        self.client.api_budget = {'requests': 0, 'maximum': 60, 'remaining': None}

    def path(self, path, method):
        if (method not in {'GET', 'POST', 'PATCH'} or not isinstance(path, str)
                or not path.startswith(self.base + '/issues')):
            raise ValueError('request API is restricted to the fixed course issues')
        relative = path[len(self.base):]
        if method == 'GET':
            allowed = (re.fullmatch(r'/issues\?state=open&labels=publication-request&sort=created&direction=asc&per_page=100&page=[1-3]', relative)
                or re.fullmatch(r'/issues/[1-9][0-9]*(?:/comments\?per_page=100&page=[1-3])?', relative))
        elif method == 'POST':
            allowed = re.fullmatch(r'/issues/[1-9][0-9]*/comments', relative)
        else:
            allowed = re.fullmatch(r'/issues/(?:comments/)?[1-9][0-9]*', relative)
        if not allowed:
            raise ValueError('unsupported publication request API operation')
        return path

    def get(self, path):
        return json.loads(self.client.get(self.path(path, 'GET'), 2 * 1024 * 1024))

    def post(self, path, body):
        return self.client.write(self.path(path, 'POST'), body, method='POST')

    def patch(self, path, body):
        return self.client.write(self.path(path, 'PATCH'), body, method='PATCH')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        raise ValueError('expected an exact UTC timestamp')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def body_hash(body):
    if not isinstance(body, str):
        raise ValueError('request body must be text')
    return hashlib.sha256(body.encode()).hexdigest()


def parse_group(body, registry):
    """Only one exact issue-form field is authority; all other text is ignored."""
    if not isinstance(body, str) or len(body.encode()) > MAX_BODY:
        return None
    headings = list(re.finditer(r'^### ([^\r\n]+)\r?$', body, re.MULTILINE))
    fields = [index for index, heading in enumerate(headings) if heading.group(1) == 'Group']
    if len(fields) != 1:
        return None
    index = fields[0]
    start = headings[index].end()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
    value = body[start:end].strip()
    if len(value) > 63 or not SLUG.fullmatch(value) or value not in {group.slug for group in registry}:
        return None
    return value


def capture_batch(api, repository, registry, cutoff):
    """api.get returns decoded JSON. Later/overflow issues remain for another run."""
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise ValueError('invalid course repository')
    before = timestamp(cutoff)
    records = []
    numbers = set()
    for page in range(1, MAX_PAGES + 1):
        issues = api.get(f'/repos/{repository}/issues?state=open&labels={LABEL}&sort=created&direction=asc&per_page={PAGE_SIZE}&page={page}')
        if not isinstance(issues, list) or len(issues) > PAGE_SIZE:
            raise RecoveryError('invalid publication request page')
        for issue in issues:
            if not isinstance(issue, dict):
                raise RecoveryError('invalid publication request')
            if 'pull_request' in issue:
                continue
            number = issue.get('number')
            if type(number) is not int or number <= 0 or number in numbers:
                raise RecoveryError('duplicate/invalid request number')
            numbers.add(number)
            if issue.get('state') != 'open' or LABEL not in [label.get('name') for label in issue.get('labels', []) if isinstance(label, dict)]:
                continue
            if timestamp(issue['created_at']) > before or timestamp(issue['updated_at']) > before:
                continue
            body = issue.get('body') or ''
            records.append({'number': number, 'slug': parse_group(body, registry), 'body_sha256': body_hash(body)})
            if len(records) == MAX_BATCH:
                return {'version': 1, 'repository': repository, 'cutoff': cutoff, 'requests': records}
        if len(issues) < PAGE_SIZE:
            break
    return {'version': 1, 'repository': repository, 'cutoff': cutoff, 'requests': records}


def validate_batch(batch, repository, registry):
    if (not isinstance(batch, dict) or set(batch) != {'version', 'repository', 'cutoff', 'requests'}
            or type(batch['version']) is not int or batch['version'] != 1 or batch['repository'] != repository
            or not isinstance(batch['requests'], list) or len(batch['requests']) > MAX_BATCH):
        raise RecoveryError('invalid request batch artifact')
    timestamp(batch['cutoff'])
    seen = set()
    slugs = {group.slug for group in registry}
    for record in batch['requests']:
        if (not isinstance(record, dict) or set(record) != {'number', 'slug', 'body_sha256'}
                or type(record['number']) is not int or record['number'] <= 0 or record['number'] in seen
                or record['slug'] is not None and record['slug'] not in slugs
                or not isinstance(record['body_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', record['body_sha256'])):
            raise RecoveryError('invalid request batch record')
        seen.add(record['number'])
    return batch


def refresh_allowed(event, now, last_refresh, *, enabled=True, api_remaining=None, interval=600):
    if event != 'issues':
        return True
    if not enabled or api_remaining is not None and api_remaining <= 200:
        return False
    current = timestamp(now)
    return last_refresh is None or (current - timestamp(last_refresh)).total_seconds() >= interval


def response(record, group_result, stage, repository, run_id, attempt, site_url):
    """A completed deploy step alone never produces a success answer."""
    if not REPOSITORY.fullmatch(repository) or not re.fullmatch(r'https://[^\s<>]+', site_url):
        raise ValueError('invalid trusted response links')
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise ValueError('invalid run identity')
    if stage == 'superseded':
        return None
    run = f'https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}'
    close = False
    if record['slug'] is None:
        text = 'The request needs one valid registered Group field. No new work was published by this check.'
        close = True
    elif stage == 'deferred':
        text = 'Refresh deferred. This request stays open for the next eligible run; no new source check was completed.'
    elif stage not in {'verified', 'unchanged_verified'}:
        text = 'The publication check did not finish successfully. Accepted archive content is not proof of publication. This request stays open for retry.'
    elif group_result is None:
        text = 'No validated group result is available. This request stays open for retry.'
    else:
        slug = record['slug']
        selected = group_result.get('selected_source')
        attempted = group_result.get('commit')
        for revision in (attempted, selected.get('commit') if selected else None):
            if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)):
                raise ValueError('invalid result revision')
        status = group_result.get('publication_status')
        error = group_result.get('error_code')
        if error in TRANSIENT:
            text = f'Group `{slug}`: source checking was deferred ({error}). Any valid accepted content is retained. This request stays open for retry.'
        elif error == 'invalid_content':
            # Error text is escaped and bounded; no student-controlled Markdown is trusted.
            detail = str(group_result.get('error', 'Content validation failed'))[:1000]
            detail = ''.join(c for c in detail if c in '\n\t' or ord(c) >= 32)
            detail = detail.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            detail = '\n'.join('    ' + line for line in detail.splitlines())
            text = f'Group `{slug}`: the attempted version was not published. Correct the content and submit another request.\n\n{detail}'
            close = True
        elif status in {'disabled', 'hidden', 'frozen'}:
            text = f'Group `{slug}` is {status}. This check did not publish new source work.'
            close = True
        elif status in {'updated', 'unchanged'} and selected:
            text = f'Group `{slug}`: selected source revision `{selected["commit"]}` is published. [Course site]({site_url.rstrip("/")}/#{quote(slug)})'
            close = True
        else:
            text = f'Group `{slug}`: no new valid publication was confirmed. This request stays open for maintainer review.'
        if attempted:
            text += f'\n\nAttempted source revision: `{attempted}`.'
        if selected:
            text += f'\nSelected source revision: `{selected["commit"]}`.'
    return {'body': MARKER + '\n' + text + f'\n\n[Publication run]({run})', 'close': close}


def answer_batch(api, batch, repository, registry, results, stage, run_id, attempt, site_url):
    """Repair one bot-owned status comment; never retry uncertain write responses."""
    validate_batch(batch, repository, registry)
    by_slug = {}
    for result in results:
        slug = result['slug']
        if slug in by_slug:
            raise RecoveryError('duplicate group result')
        by_slug[slug] = result
    answered = []
    for record in batch['requests']:
        base = f'/repos/{repository}/issues/{record["number"]}'
        issue = api.get(base)
        if (not isinstance(issue, dict) or issue.get('state') != 'open' or 'pull_request' in issue
                or issue.get('number') != record['number']
                or body_hash(issue.get('body') or '') != record['body_sha256']
                or LABEL not in [label.get('name') for label in issue.get('labels', []) if isinstance(label, dict)]):
            continue
        if parse_group(issue.get('body') or '', registry) != record['slug']:
            continue
        reply = response(record, by_slug.get(record['slug']), stage, repository, run_id, attempt, site_url)
        if reply is None:
            continue
        existing = []
        exhausted = True
        for page in range(1, MAX_PAGES + 1):
            comments = api.get(base + f'/comments?per_page={PAGE_SIZE}&page={page}')
            if not isinstance(comments, list) or len(comments) > PAGE_SIZE:
                raise RecoveryError('invalid request comments')
            existing.extend(comment for comment in comments if isinstance(comment, dict)
                            and comment.get('user', {}).get('login') == 'github-actions[bot]'
                            and comment.get('user', {}).get('type') == 'Bot'
                            and isinstance(comment.get('body'), str) and comment['body'].startswith(MARKER))
            if len(comments) < PAGE_SIZE:
                exhausted = False
                break
        # An unobserved existing marker could be beyond the bound; do not duplicate it.
        if exhausted or len(existing) > 1:
            continue
        if existing:
            comment = existing[0]
            if type(comment.get('id')) is not int or comment['id'] <= 0:
                raise RecoveryError('invalid status comment identity')
            # Identical outcomes across retry runs do not churn a status comment.
            previous_outcome = comment['body'].split('\n\n[Publication run](')[0]
            next_outcome = reply['body'].split('\n\n[Publication run](')[0]
            if previous_outcome != next_outcome:
                api.patch(f'/repos/{repository}/issues/comments/{comment["id"]}', {'body': reply['body']})
        else:
            api.post(base + '/comments', {'body': reply['body']})
        # Recheck edited body immediately before closing. A later edit is for the next sweep.
        latest = api.get(base)
        if (reply['close'] and body_hash(latest.get('body') or '') == record['body_sha256']
                and latest.get('state') == 'open' and latest.get('number') == record['number']
                and LABEL in [label.get('name') for label in latest.get('labels', []) if isinstance(label, dict)]):
            api.patch(base, {'state': 'closed', 'state_reason': 'completed'})
        answered.append(record['number'])
    return answered
