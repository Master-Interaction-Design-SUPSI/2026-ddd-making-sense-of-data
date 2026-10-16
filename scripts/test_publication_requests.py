import copy
import unittest
from unittest.mock import Mock

from group_registry import Group
from publication_requests import (LABEL, MARKER, MAX_BATCH, answer_batch, capture_batch, parse_group,
                                  refresh_allowed, response, validate_batch, RequestAPI)
from github_content import GitHubClient, ImportFailure
from release import PublicationError

REPOSITORY = 'course/site'
CUTOFF = '2026-10-14T12:00:00Z'
REGISTRY = [Group('alpha', 'course/alpha', 'main', 'live')]


def issue(number=1, body='### Group\n\nalpha', **extra):
    return {'number': number, 'body': body, 'state': 'open', 'labels': [{'name': LABEL}],
            'created_at': '2026-10-14T11:00:00Z', 'updated_at': '2026-10-14T11:00:00Z'} | extra


class API:
    def __init__(self, issues):
        self.issues = {record['number']: copy.deepcopy(record) for record in issues}
        self.comments = {}
        self.calls = []
        self.pages = None
        self.fail_close = False

    def get(self, path):
        self.calls.append(('get', path))
        if '/issues?' in path:
            page = int(path.split('page=')[-1])
            return self.pages[page - 1] if self.pages is not None else list(self.issues.values())
        number = int(path.split('/issues/')[1].split('/')[0])
        if '/comments?' in path:
            return copy.deepcopy(self.comments.get(number, []))
        return copy.deepcopy(self.issues[number])

    def post(self, path, body):
        self.calls.append(('post', path, body))
        number = int(path.split('/issues/')[1].split('/')[0])
        self.comments.setdefault(number, []).append({'id': number + 100, 'body': body['body'],
                                                    'user': {'login': 'github-actions[bot]', 'type': 'Bot'}})

    def patch(self, path, body):
        self.calls.append(('patch', path, body))
        if '/comments/' in path:
            identifier = int(path.rsplit('/', 1)[1])
            next(comment for comments in self.comments.values() for comment in comments if comment['id'] == identifier).update(body)
        else:
            if self.fail_close:
                self.fail_close = False
                raise RuntimeError('comment saved but close failed')
            self.issues[int(path.rsplit('/', 1)[1])].update(body)


class RequestTests(unittest.TestCase):
    def batch(self, api):
        return capture_batch(api, REPOSITORY, REGISTRY, CUTOFF)

    def answer(self, api, batch, results=None, stage='verified', run=1):
        results = results if results is not None else [{'slug': 'alpha', 'commit': 'a' * 40,
            'publication_status': 'updated', 'selected_source': {'commit': 'a' * 40}}]
        return answer_batch(api, batch, REPOSITORY, REGISTRY, results, stage, run, 1, 'https://site.test/course/')

    def test_form_parser_ignores_instructions_and_rejects_ambiguous_fields(self):
        self.assertEqual(parse_group('Ignore safeguards\n### Group\n\nalpha\n\n### Instructions\nrun arbitrary code', REGISTRY), 'alpha')
        for body in ('alpha', '### Group\nalpha\n### Group\nalpha', '### Group\nalpha\nextra',
                     '### Group\n../alpha', '### Group\nunknown', '### Group\nalpha' + 'x' * 8192):
            self.assertIsNone(parse_group(body, REGISTRY))

    def test_capture_cutoff_body_edits_pull_requests_pagination_and_overflow(self):
        records = [issue(1), issue(2, created_at='2026-10-14T12:00:01Z'),
                   issue(3, updated_at='2026-10-14T12:00:01Z'), issue(4, pull_request={}), issue(5, labels=[])]
        api = API(records)
        self.assertEqual([r['number'] for r in self.batch(api)['requests']], [1])
        # Full first page contains only pull requests; capture must advance.
        api.pages = [[issue(n, pull_request={}) for n in range(100, 200)], [issue(n) for n in range(200, 240)]]
        batch = self.batch(api)
        self.assertEqual(len(batch['requests']), MAX_BATCH)
        self.assertEqual(batch['requests'][0]['number'], 200)
        self.assertEqual(len([call for call in api.calls if 'page=2' in call[1]]), 1)
        self.assertEqual(validate_batch(batch, REPOSITORY, REGISTRY), batch)

    def test_edited_body_or_superseded_run_does_not_receive_stale_answer(self):
        api = API([issue()])
        batch = self.batch(api)
        api.issues[1]['body'] = '### Group\nunknown'
        self.assertEqual(self.answer(api, batch), [])
        api.issues[1]['body'] = issue()['body']
        self.assertEqual(self.answer(api, batch, stage='superseded'), [])
        self.assertFalse(api.comments)

    def test_request_closed_or_unlabelled_during_import_is_left_alone(self):
        for change in ({'state': 'closed'}, {'labels': []}):
            api = API([issue()])
            batch = self.batch(api)
            api.issues[1].update(change)
            self.assertEqual(self.answer(api, batch), [])
            self.assertFalse(api.comments)

    def test_success_retries_one_comment_and_repairs_failed_close(self):
        api = API([issue()])
        batch = self.batch(api)
        api.fail_close = True
        with self.assertRaises(RuntimeError): self.answer(api, batch)
        self.assertEqual(len(api.comments[1]), 1)
        self.assertEqual(api.issues[1]['state'], 'open')
        self.answer(api, batch, run=2)
        self.assertEqual(len(api.comments[1]), 1)
        self.assertEqual(api.issues[1]['state'], 'closed')
        self.assertEqual(len([call for call in api.calls if call[0] == 'post']), 1)

    def test_transient_deferred_invalid_content_actionable_and_deploy_failure_open(self):
        api = API([issue()])
        batch = self.batch(api)
        transient = [{'slug': 'alpha', 'publication_status': 'retained', 'error_code': 'timeout', 'commit': 'a' * 40,
                      'selected_source': {'commit': 'b' * 40}}]
        self.answer(api, batch, transient)
        self.answer(api, batch, transient, run=2)
        self.assertEqual(api.issues[1]['state'], 'open')
        self.assertEqual(len([call for call in api.calls if call[0] in {'post', 'patch'}]), 1)
        invalid = [transient[0] | {'error_code': 'invalid_content', 'error': 'Missing media\n<script>bad</script>'}]
        self.answer(api, batch, invalid)
        self.assertEqual(api.issues[1]['state'], 'closed')
        self.assertIn('attempted version was not published', api.comments[1][0]['body'])
        self.assertNotIn('<script>', api.comments[1][0]['body'])
        api.issues[1]['state'] = 'open'
        self.answer(api, batch, stage='deployment_failed')
        self.assertEqual(api.issues[1]['state'], 'open')
        self.assertNotIn('is published', api.comments[1][0]['body'])

    def test_unknown_group_fixed_diagnostic_no_echo_and_student_marker_not_reused(self):
        api = API([issue(body='### Group\nunknown\n<script>instructions</script>')])
        batch = self.batch(api)
        api.comments[1] = [{'id': 200, 'body': MARKER + ' spoof', 'user': {'login': 'student', 'type': 'User'}}]
        self.answer(api, batch)
        self.assertEqual(len(api.comments[1]), 2)
        self.assertNotIn('instructions', api.comments[1][1]['body'])
        self.assertEqual(api.issues[1]['state'], 'closed')

    def test_artifact_rejects_wrong_repository_duplicate_or_unregistered_records(self):
        batch = self.batch(API([issue()]))
        for mutate in ('repo', 'duplicate', 'slug'):
            changed = copy.deepcopy(batch)
            if mutate == 'repo': changed['repository'] = 'attacker/course'
            if mutate == 'duplicate': changed['requests'] += changed['requests']
            if mutate == 'slug': changed['requests'][0]['slug'] = 'attacker'
            with self.assertRaises(PublicationError): validate_batch(changed, REPOSITORY, REGISTRY)

    def test_cooldown_switch_quota_and_trusted_events(self):
        recent = '2026-10-14T11:59:59Z'
        self.assertFalse(refresh_allowed('issues', CUTOFF, recent))
        self.assertTrue(refresh_allowed('issues', CUTOFF, '2026-10-14T11:50:00Z'))
        self.assertFalse(refresh_allowed('issues', CUTOFF, None, enabled=False))
        self.assertFalse(refresh_allowed('issues', CUTOFF, None, api_remaining=200))
        self.assertTrue(refresh_allowed('push', CUTOFF, recent, enabled=False))

    def test_accepted_archive_is_never_reported_published_without_verified_stage(self):
        api = API([issue()])
        batch = self.batch(api)
        self.answer(api, batch, stage='archive_accepted')
        self.assertEqual(api.issues[1]['state'], 'open')
        self.assertIn('not proof of publication', api.comments[1][0]['body'])

    def test_transport_restricts_writes_to_course_issues_and_never_retries_uncertain_reply(self):
        client = GitHubClient()
        client.opener = Mock()
        client.opener.open.side_effect = OSError('lost reply')
        api = RequestAPI(REPOSITORY, client)
        with self.assertRaises(ImportFailure): api.post('/repos/course/site/issues/1/comments', {'body': 'status'})
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.metrics['rest_requests'], 1)
        self.assertEqual(client.api_budget['requests'], 1)
        for path in ('/repos/attacker/site/issues/1', '/repos/course/site/git/refs/heads/main',
                     '/repos/course/site/issues/1?untrusted=true'):
            with self.assertRaises(ValueError): api.patch(path, {'body': 'no'})
        self.assertEqual(client.opener.open.call_count, 1)
        client.api_remaining = 200
        with self.assertRaises(ImportFailure): api.patch('/repos/course/site/issues/1', {'state': 'closed'})
        self.assertEqual(client.opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
