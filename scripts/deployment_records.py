"""Keep publication expectations in GitHub Deployments, independently of the CDN."""

import json
from urllib.request import Request

from github_content import GitHubClient
from group_registry import REPOSITORY


TASK = "course-publication"
ENVIRONMENT = "course-publication-check"


class DeploymentRecords:
    def __init__(self, repository):
        if not REPOSITORY.fullmatch(repository):
            raise ValueError("Expected the main repository as owner/name")
        self.base = "/repos/" + repository
        self.client = GitHubClient()

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

    def post(self, path, body):
        # Do not retry a write: a lost response may still have created the record.
        request = Request("https://api.github.com" + self.base + path,
                          data=json.dumps(body).encode(), method="POST",
                          headers={**self.client.headers, "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json"})
        with self.client.opener.open(request, timeout=15) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("GitHub deployment response exceeds 2 MiB")
        return json.loads(payload)

    def create(self, release):
        record = self.post("/deployments", {
            "ref": release["source_commit"], "task": TASK, "environment": ENVIRONMENT,
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
