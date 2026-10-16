import contextlib
import io
import json
import os
import shutil
import tarfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import build_site
import import_groups
import publish_site
import test_publication_state as fixtures
from deployment_records import DeploymentRecords, ENVIRONMENT, TASK
from publication_state import MANIFEST, RecoveryError


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.records = Mock()
        self.records.main_commit.return_value = "a" * 40
        self.records.latest.return_value = None
        self.records.create.return_value = 123
        for name, value in (("ROOT", self.fixture.root),
                            ("STATE", self.fixture.root / ".scratch/publication-run.json"),
                            ("REPORT", self.fixture.root / ".scratch/group-import-report.json")):
            self.patch("publish_site." + name, value)
        self.patch("publish_site.git_revision", side_effect=lambda ref: "a" * 40 if ref == "HEAD" else "b" * 40)
        self.patch("publish_site.time.sleep")
        self.patch("publish_site.worktree_clean", return_value=True)
        self.patch("build_site.import_groups", side_effect=lambda groups, destination, **kwargs:
                   import_groups.import_groups(groups, destination, self.fixture.api.client, **kwargs))
        environment = patch.dict(os.environ, GITHUB_REPOSITORY="course/site", GITHUB_RUN_ID="50",
                                 GITHUB_RUN_ATTEMPT="1", GITHUB_OUTPUT="", GITHUB_STEP_SUMMARY="")
        environment.start()
        self.addCleanup(environment.stop)
        self.url = "https://site.test/course/"

    def patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def state(self):
        return json.loads(publish_site.STATE.read_bytes())

    def prepare(self, first=False, retry=False):
        publish_site.prepare(self.records, self.url, first, retry)

    def record(self):
        publish_site.record_candidate(self.records)
        state = self.state()
        self.records.latest.return_value = {"id": 123, "payload": state["release"]}

    def bootstrap(self):
        self.prepare(first=True)
        self.record()
        self.fixture.deploy()
        publish_site.verify(self.records)

    def archive(self):
        archive = self.fixture.workspace / "artifact.tar"
        with tarfile.open(archive, "w") as target:
            target.add(self.fixture.output, arcname=".")
        return archive

    def test_bootstrap_requires_explicit_input_and_both_state_files_absent(self):
        with self.assertRaisesRegex(RecoveryError, "initial publication"):
            self.prepare()
        self.records.create.assert_not_called()
        (self.fixture.live / MANIFEST).write_text('{"version":1,"groups":{}}')
        with self.assertRaisesRegex(RecoveryError, "already exists"):
            self.prepare(first=True)
        (self.fixture.live / MANIFEST).unlink()
        (self.fixture.live / "release.json").write_text("{}")
        with self.assertRaisesRegex(RecoveryError, "already exists"):
            self.prepare(first=True)

    def test_bootstrap_does_not_treat_network_failure_as_an_empty_site(self):
        with patch.object(self.fixture.server, "open", side_effect=HTTPError(self.url, 503, "down", {}, None)):
            with self.assertRaises(RecoveryError):
                self.prepare(first=True)
        self.records.create.assert_not_called()

    def test_unchanged_refresh_does_not_need_another_deployment(self):
        self.bootstrap()
        self.prepare()
        self.assertEqual(self.state()["status"], "unchanged")
        self.assertEqual([r["publication_status"] for r in self.fixture.report()], ["unchanged", "unchanged"])
        self.assertEqual(self.records.create.call_count, 1)

    def test_v1_publisher_cannot_replace_or_artifact_retry_archive_authority(self):
        release = {'version': 2, 'run_id': '50', 'run_attempt': '1', 'main_commit': 'a' * 40,
            'main_tree': 'b' * 40, 'archive_commit': 'c' * 40, 'archive_manifest_sha256': 'd' * 64,
            'served_manifest_sha256': 'e' * 64, 'site_sha256': 'f' * 64, 'probes': []}
        self.records.latest.return_value = {'id': 123, 'payload': release}
        with self.assertRaisesRegex(RecoveryError, 'Archive authority'):
            self.prepare()
        with self.assertRaisesRegex(RecoveryError, 'durable main/archive'):
            self.prepare(retry=True)
        self.records.create.assert_not_called()

    def test_changed_group_publishes_while_invalid_group_retains_its_snapshot(self):
        self.bootstrap()
        original = self.fixture.bytes_at(self.fixture.live / "beta")
        self.fixture.update("alpha", b"new alpha")
        self.fixture.update("beta", visual=b"invalid")
        self.prepare()
        self.assertEqual(self.state()["status"], "prepared")
        self.assertEqual([r["publication_status"] for r in self.fixture.report()], ["updated", "retained"])
        self.assertEqual(self.fixture.bytes_at(self.fixture.output / "beta"), original)
        self.record()
        self.fixture.deploy()
        publish_site.verify(self.records)
        self.assertEqual(self.state()["status"], "verified")

    def test_code_or_registry_tree_change_redeploys_even_if_output_matches(self):
        self.bootstrap()
        with patch("publish_site.git_revision", side_effect=lambda ref: "a" * 40 if ref == "HEAD" else "c" * 40):
            self.prepare()
        self.assertEqual(self.state()["status"], "prepared")

    def test_local_site_file_change_redeploys(self):
        self.bootstrap()
        (self.fixture.root / "index.html").write_text("new trusted code")
        with patch("publish_site.worktree_clean", return_value=False):
            self.prepare()
        self.assertEqual(self.state()["status"], "prepared")

    def test_disabled_and_removed_groups_change_the_publication(self):
        self.bootstrap()
        self.fixture.registry("beta", disabled=("beta",))
        self.prepare()
        self.assertEqual(self.state()["status"], "prepared")
        self.assertEqual(json.loads((self.fixture.output / "entries.json").read_text()), [])

    def test_obsolete_run_cannot_prepare_or_record_a_deployment(self):
        self.records.main_commit.return_value = "c" * 40
        self.prepare()
        self.assertEqual(self.state()["status"], "superseded")
        self.records.latest.assert_not_called()
        self.records.main_commit.return_value = "a" * 40
        self.prepare(first=True)
        self.records.main_commit.return_value = "c" * 40
        publish_site.record_candidate(self.records)
        self.assertEqual(self.state()["status"], "superseded")
        self.records.create.assert_not_called()

    def test_pause_blocks_normal_preparation_and_mutation_before_any_remote_call(self):
        self.records.reset_mock()
        with patch.dict(os.environ, DDD_PUBLICATION_PAUSED='true'):
            for operation in (lambda: publish_site.prepare(self.records, self.url),
                              lambda: publish_site.record_candidate(self.records),
                              lambda: publish_site.restore_candidate(self.records, self.fixture.workspace / 'unused.tar'),
                              lambda: publish_site.verify(self.records)):
                with self.subTest(operation=operation), self.assertRaisesRegex(RecoveryError, 'paused'):
                    operation()
        self.records.main_commit.assert_not_called()
        self.records.latest.assert_not_called()
        self.records.create.assert_not_called()
        self.records.status.assert_not_called()

    def test_coherent_old_site_blocks_the_next_build_after_a_failed_deployment(self):
        self.bootstrap()
        self.fixture.update("alpha")
        self.prepare()
        self.record()
        self.records.status.reset_mock()
        with self.assertRaisesRegex(RecoveryError, "differs"):
            publish_site.verify(self.records)
        self.assertEqual(self.state()["status"], "unverified")
        self.records.status.assert_not_called()
        with self.assertRaisesRegex(RecoveryError, "differs"):
            self.prepare()
        self.assertEqual(self.records.create.call_count, 2)

    def test_retry_restores_exact_candidate_despite_newer_student_edits(self):
        self.bootstrap()
        self.fixture.update("alpha", b"pending candidate")
        self.prepare()
        self.record()
        expected = self.fixture.bytes_at(self.fixture.output)
        archive = self.archive()
        shutil.rmtree(self.fixture.output)
        self.fixture.update("alpha", b"newer work")
        with patch.dict(os.environ, GITHUB_RUN_ATTEMPT="2"), patch("publish_site.output") as outputs:
            self.prepare(retry=True)
        outputs.assert_any_call("artifact_run", "50")
        outputs.assert_any_call("artifact_attempt", "1")
        publish_site.restore_candidate(self.records, archive)
        self.assertEqual(self.fixture.bytes_at(self.fixture.output), expected)
        self.fixture.deploy()
        publish_site.verify(self.records)
        self.assertEqual(self.state()["status"], "verified")
        self.assertEqual(self.records.create.call_count, 2)
        self.prepare()
        self.assertEqual((self.fixture.output / "alpha/text.md").read_bytes(), b"newer work")

    def test_retry_rejects_tampered_candidate_and_traversal(self):
        self.prepare(first=True)
        self.record()
        (self.fixture.output / "index.html").write_text("tampered")
        archive = self.archive()
        shutil.rmtree(self.fixture.output)
        self.prepare(retry=True)
        with self.assertRaisesRegex(RecoveryError, "does not match"):
            publish_site.restore_candidate(self.records, archive)
        self.assertFalse(self.fixture.output.exists())
        with tarfile.open(archive, "w") as target:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            target.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(ValueError, "unsafe paths"):
            publish_site.restore_candidate(self.records, archive)
        self.assertFalse((self.fixture.root / "escape").exists())

    def test_record_rejects_a_candidate_changed_after_preparation(self):
        self.prepare(first=True)
        (self.fixture.output / "index.html").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Candidate changed"):
            self.record()
        self.records.create.assert_not_called()

    def test_bootstrap_is_never_a_bypass_for_existing_records(self):
        self.bootstrap()
        with self.assertRaisesRegex(RecoveryError, "record already exists"):
            self.prepare(first=True)
        with self.assertRaisesRegex(ValueError, "not both"):
            self.prepare(first=True, retry=True)

    def test_manifest_is_pinned_between_preflight_and_build(self):
        self.bootstrap()
        expected = self.records.latest.return_value["payload"]["manifest_sha256"]
        (self.fixture.live / MANIFEST).write_text('{"version":1,"groups":{}}')
        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
            build_site.main(import_remote=True, published_url=self.url, expected_manifest_sha256=expected)

    def test_served_verification_has_one_overall_deadline_across_retries(self):
        self.bootstrap()
        release = self.state()["release"]
        clock = {"seconds": 0}

        def expired_read(*arguments):
            clock["seconds"] = 181
            raise RecoveryError("stale release")

        with patch("publish_site.time.monotonic", side_effect=lambda: clock["seconds"]), \
                patch("publish_site.SiteReader") as reader, patch("publish_site.time.sleep") as sleep:
            reader.return_value.read.side_effect = expired_read
            with self.assertRaisesRegex(RecoveryError, "stale release"):
                publish_site.served_release(self.url, release)
        self.assertEqual(reader.call_count, 1)
        sleep.assert_not_called()

    def test_final_release_metadata_is_included_in_candidate_byte_ceiling(self):
        enforce = build_site.enforce_site_budget
        calls = []

        def budget(candidate):
            calls.append((candidate / publish_site.RELEASE).is_file())
            if calls[-1]:
                raise ValueError("Final metadata exceeds site limit")
            return enforce(candidate)

        with patch("build_site.enforce_site_budget", side_effect=budget):
            with self.assertRaisesRegex(ValueError, "Final metadata"):
                self.prepare(first=True)
        self.assertEqual(calls, [False, True])
        self.assertEqual(self.state()["status"], "failed")
        self.records.create.assert_not_called()

    def test_summary_separates_candidate_status_and_escapes_errors(self):
        self.bootstrap()
        publish_site.REPORT.write_text(json.dumps({"groups": [{
            "slug": "alpha", "status": "failed", "publication_status": "retained",
            "selected_source": {"commit": "a" * 40}, "commit": "b" * 40,
            "error": "<script>x</script>|bad\nrow",
        }]}))
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            publish_site.summary()
        text = captured.getvalue()
        self.assertIn("verified", text)
        self.assertIn("retained", text)
        self.assertIn("a" * 40, text)
        self.assertIn("b" * 40, text)
        self.assertNotIn("<script>", text)
        self.assertIn("&#124;", text)


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
        self.assertEqual(records.create({"source_commit": "a" * 40}), 2)
        body = records.post.call_args.args[1]
        self.assertFalse(body["auto_merge"])
        self.assertEqual(body["required_contexts"], [])

    def test_ambiguous_write_is_not_retried(self):
        records = DeploymentRecords("course/site")
        records.client.opener = Mock()
        records.client.opener.open.side_effect = OSError("lost response")
        with self.assertRaises(OSError):
            records.create({"source_commit": "a" * 40})
        self.assertEqual(records.client.opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
