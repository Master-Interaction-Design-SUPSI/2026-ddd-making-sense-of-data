import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from deployment_records import DeploymentRecords, ENVIRONMENT, TASK
from github_content import ImportFailure
from test_fixtures import Response


class DeploymentRecordTests(unittest.TestCase):
    def test_reads_only_the_publication_task_and_records_without_merging(self):
        records = DeploymentRecords("course/site")
        records.client = Mock()
        records.client.get.return_value = json.dumps([{
            "id": 1, "task": TASK, "environment": ENVIRONMENT, "payload": {},
        }]).encode()
        self.assertEqual(records.latest()["id"], 1)
        path = records.client.get.call_args.args[0]
        self.assertIn("task=course-publication", path)
        self.assertIn("per_page=1", path)
        records.post = Mock(return_value={"id": 2})
        self.assertEqual(records.create({"main_commit": "a" * 40}), 2)
        body = records.post.call_args.args[1]
        self.assertEqual(body["ref"], "a" * 40)
        self.assertFalse(body["auto_merge"])
        self.assertEqual(body["required_contexts"], [])

    def test_ambiguous_write_is_not_retried(self):
        records = DeploymentRecords("course/site")
        records.client.opener = Mock()
        records.client.opener.open.side_effect = OSError("lost response")
        with self.assertRaises(OSError):
            records.create({"main_commit": "a" * 40})
        self.assertEqual(records.client.opener.open.call_count, 1)

    def test_missing_archive_branch_is_distinct_from_an_unreadable_one(self):
        records = DeploymentRecords("course/site")
        records.client.opener = Mock()
        records.client.opener.open.return_value = Response(json.dumps({"object": {"sha": "c" * 40}}).encode())
        self.assertEqual(records.archive_commit(), "c" * 40)
        records.client.opener.open.side_effect = HTTPError("url", 404, "missing", {}, None)
        self.assertIsNone(records.archive_commit())
        records.client.opener.open.side_effect = HTTPError("url", 401, "denied", {}, None)
        with self.assertRaises(ImportFailure):
            records.archive_commit()


if __name__ == "__main__":
    unittest.main()
