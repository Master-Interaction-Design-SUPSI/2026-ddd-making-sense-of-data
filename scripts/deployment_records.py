"""Keep publication expectations in GitHub Deployments, independently of the CDN."""

import json
from urllib.request import Request

from github_content import GitHubClient, ImportFailure
from group_registry import REPOSITORY


TASK = "course-publication"
ENVIRONMENT = "course-publication-check"


class DeploymentRecords:
    def __init__(self, repository):
        if not REPOSITORY.fullmatch(repository):
            raise ValueError("Expected the main repository as owner/name")
        self.base = "/repos/" + repository
        self.client = GitHubClient()
        self.client.api_budget = {'requests': 0, 'maximum': 40, 'remaining': None}

    def latest(self):
        payload = self.client.get(
            self.base + f"/deployments?task={TASK}&environment={ENVIRONMENT}&per_page=1", 2 * 1024 * 1024
        )
        records = json.loads(payload)
        if not isinstance(records, list) or len(records) > 1:
            raise ValueError("GitHub returned an invalid deployment list")
        if not records:
            return None
        record = records[0]
        if (type(record.get("id")) is not int or record.get("task") != TASK
                or record.get("environment") != ENVIRONMENT):
            raise ValueError("GitHub returned an unexpected deployment record")
        return record

    def main_commit(self):
        return self.client.json(self.base + "/git/ref/heads/main")["object"]["sha"]

    def archive_commit(self):
        return self.client.json(self.base + '/git/ref/heads/student-work')['object']['sha']

    def archive_main(self, commit):
        import re
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Expected an exact archive commit')
        record = self.client.json(self.base + '/git/commits/' + commit)
        if record.get('sha') != commit or not isinstance(record.get('message'), str):
            raise ValueError('Unexpected archive acceptance record')
        pins = re.findall(r'^Main-Commit: ([0-9a-f]{40})$', record['message'], re.MULTILINE)
        if len(pins) != 1:
            raise ValueError('Archive acceptance has no unambiguous main identity')
        return pins[0]

    def archive_authority(self, commit):
        import re
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Expected an exact archive commit')
        record = self.client.json(self.base + '/git/commits/' + commit)
        if record.get('sha') != commit or not isinstance(record.get('message'), str):
            raise ValueError('Unexpected archive authority record')
        values = [line.removeprefix('Archive-Authority: ') for line in record['message'].splitlines()
                  if line.startswith('Archive-Authority: ')]
        if len(values) != 1 or values[0] not in {'v1-seed', 'v2'}:
            raise ValueError('Archive has no unambiguous durable authority marker')
        return values[0]

    def is_verified(self, identifier):
        if type(identifier) is not int or identifier <= 0:
            raise ValueError('Invalid deployment identity')
        statuses = json.loads(self.client.get(self.base + f'/deployments/{identifier}/statuses?per_page=10',
                                              2 * 1024 * 1024))
        if not isinstance(statuses, list) or len(statuses) > 10:
            raise ValueError('Invalid deployment statuses')
        return any(isinstance(status, dict) and status.get('state') == 'success' for status in statuses)

    def latest_verified(self):
        """Bounded history lookup; an attempted deployment is never verification."""
        records = json.loads(self.client.get(self.base +
            f'/deployments?task={TASK}&environment={ENVIRONMENT}&per_page=20', 2 * 1024 * 1024))
        if not isinstance(records, list) or len(records) > 20:
            raise ValueError('Invalid deployment history')
        for record in records:
            if (not isinstance(record, dict) or type(record.get('id')) is not int or record['id'] <= 0
                    or record.get('task') != TASK or record.get('environment') != ENVIRONMENT):
                raise ValueError('Unexpected deployment history record')
            if self.is_verified(record['id']):
                return record
        if len(records) == 20:
            raise ValueError('No verified release in bounded history; maintainer reconciliation required')
        return None

    def post(self, path, body):
        # Do not retry a write: a lost response may still have created the record.
        self.client.remaining()
        budget = self.client.api_budget
        if (self.client.api_remaining is not None and self.client.api_remaining <= 200
                or budget['requests'] >= budget['maximum']
                or budget['remaining'] is not None and budget['remaining'] <= 200):
            raise ImportFailure('rate_limited', 'Preserving deployment API headroom')
        budget['requests'] += 1
        self.client.metrics['rest_requests'] += 1
        request = Request("https://api.github.com" + self.base + path,
                          data=json.dumps(body).encode(), method="POST",
                          headers={**self.client.headers, "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json"})
        with self.client.opener.open(request, timeout=min(15, self.client.remaining())) as response:
            remaining = response.headers.get('X-RateLimit-Remaining')
            if remaining and remaining.isdigit():
                self.client.api_remaining = int(remaining)
                budget['remaining'] = self.client.api_remaining
            payload = response.read(2 * 1024 * 1024 + 1)
            self.client.metrics['rest_bytes'] += len(payload)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("GitHub deployment response exceeds 2 MiB")
        return json.loads(payload)

    def create(self, release):
        record = self.post("/deployments", {
            "ref": release['main_commit'] if release.get('version', 1) == 2 else release["source_commit"],
            "task": TASK, "environment": ENVIRONMENT,
            "auto_merge": False, "required_contexts": [], "payload": release,
            "description": "Expected course publication; verify served files before the next build",
            "production_environment": False,
        })
        if type(record.get("id")) is not int:
            raise ValueError("GitHub did not create the expected deployment record")
        return record["id"]

    def status(self, identifier, state, run_url):
        if type(identifier) is not int or state not in {"in_progress", "success"}:
            raise ValueError("Invalid publication status")
        self.post(f"/deployments/{identifier}/statuses", {
            "state": state, "log_url": run_url, "auto_inactive": False,
            "description": "Course publication " + ("verified" if state == "success" else "awaiting verification"),
        })
