#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("jira_rank_cache.py")
SPEC = importlib.util.spec_from_file_location("jira_rank_cache", SCRIPT)
assert SPEC and SPEC.loader
cache_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cache_mod)


def jira_issue(
    key: str,
    summary: str,
    updated: str = "2026-10-01T12:00:00.000+0000",
    parent_key: str | None = None,
) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "issuetype": {"name": "Story"},
            "status": {"name": "Selected for Development"},
            "priority": {"name": "Medium"},
            "labels": ["ready"],
            "assignee": None,
            "updated": updated,
            "description": {"type": "doc", "content": []},
            "comment": {"comments": []},
            "issuelinks": [],
            "parent": {"key": parent_key} if parent_key else None,
        },
    }


def summary_record(key: str) -> dict:
    return {
        "key": key,
        "analysis": {
            "disposition": "promising",
            "preliminary_complexity": 1,
            "assessment_confidence": 2,
            "reasons": ["Specific outcome"],
        },
    }


def full_analysis(*, gates: list[dict] | None = None) -> dict:
    return {
        "hard_gates": gates or [],
        "dimensions": {
            "decision_ambiguity": 1,
            "implementation_uncertainty": 2,
            "validation_uncertainty": 1,
            "dependency_uncertainty": 1,
            "blast_radius": 2,
        },
        "assessment_confidence": 4,
        "inferred_repositories": ["example"],
        "implementation_path": "Extend the existing pattern",
        "validation_path": "Focused unit test",
        "evidence": ["Canonical pattern exists"],
        "refinement_questions": [],
    }


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix=".jira-rank-cache-test-", dir=Path(__file__).resolve().parent.parent
        )
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache-dir" / "cache.json"
        self.rubric = self.root / "rubric.md"
        self.rubric.write_text("rubric v1\n", encoding="utf-8")
        self.filter = {
            "id": "21689",
            "name": "Fax Issues For Pickup Service",
            "jql": "project = FAX AND assignee = EMPTY",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, **values: object) -> argparse.Namespace:
        defaults = {
            "cache": str(self.cache),
            "rubric": str(self.rubric),
            "filter_id": "21689",
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def capture(self, func, args) -> dict:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            func(args)
        return json.loads(stream.getvalue())

    def sync(self, members: list[dict], delta: list[dict] | None = None) -> dict:
        side_effect = [members]
        if self.cache.exists():
            side_effect.append(delta or [])
        with (
            mock.patch.object(cache_mod, "run_json", return_value=self.filter),
            mock.patch.object(cache_mod, "jira_search", side_effect=side_effect),
        ):
            return self.capture(cache_mod.sync, self.args())

    def record(self, stage: str, records: list[dict]) -> dict:
        source = self.root / f"{stage}.json"
        source.write_text(json.dumps(records), encoding="utf-8")
        return self.capture(cache_mod.record, self.args(stage=stage, input=str(source)))

    def fetch_issue(self, payload: dict) -> dict:
        with (
            mock.patch.object(cache_mod, "require_tool", return_value="acli"),
            mock.patch.object(cache_mod, "run_json", return_value=payload),
        ):
            return self.capture(cache_mod.issue, self.args(key=payload["key"]))

    def make_repo(self) -> tuple[Path, str]:
        repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
        )
        (repo / "file.txt").write_text("content\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/master", sha],
            check=True,
        )
        return repo, sha

    def code_analysis(
        self, repo: Path, sha: str, *, gates: list[dict] | None = None
    ) -> dict:
        analysis = full_analysis(gates=gates)
        analysis.update(
            {
                "repositories": [
                    {
                        "name": "example",
                        "path": str(repo),
                        "default_ref": "origin/master",
                        "local_sha": sha,
                        "remote_sha": sha,
                    }
                ],
                "active_prs": [],
                "runtime_profile": "dox-agent-v1",
            }
        )
        return analysis

    def test_cold_sync_writes_secure_cache_and_summary_queue(self) -> None:
        result = self.sync([jira_issue("FAX-1", "Small behavior")])
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.cache.parent.stat().st_mode), 0o700)

        queued = self.capture(cache_mod.queue, self.args(stage="summary", limit=None))
        self.assertEqual([item["key"] for item in queued["candidates"]], ["FAX-1"])

    def test_summary_queue_defaults_to_fifty_candidates(self) -> None:
        issues = [
            jira_issue(f"FAX-{index}", f"Issue {index}") for index in range(1, 61)
        ]
        self.sync(issues)
        queued = self.capture(cache_mod.queue, self.args(stage="summary", limit=None))
        self.assertEqual(queued["count"], 50)
        self.assertEqual(queued["candidates"][0]["key"], "FAX-1")

    def test_excluded_epic_is_persisted_and_skipped_by_queue(self) -> None:
        excluded = jira_issue("FAX-1", "Provider work", parent_key="FAX-2782")
        included = jira_issue("FAX-2", "Standalone work")
        result = self.capture(
            cache_mod.policy, self.args(add_epic="FAX-2782", remove_epic=None)
        )
        self.assertEqual(result["excluded_epics"], ["FAX-2782"])
        with (
            mock.patch.object(cache_mod, "run_json", return_value=self.filter),
            mock.patch.object(
                cache_mod, "jira_search", side_effect=[[excluded, included], [excluded]]
            ),
        ):
            self.capture(cache_mod.sync, self.args())
        queued = self.capture(cache_mod.queue, self.args(stage="summary", limit=None))
        self.assertEqual([item["key"] for item in queued["candidates"]], ["FAX-2"])
        self.assertEqual(queued["excluded_by_epic"], 1)
        self.capture(cache_mod.clear, self.args(yes=True))
        self.assertTrue((self.cache.parent / "policy.json").exists())

    def test_policy_dry_run_lists_affected_keys_without_writing(self) -> None:
        with mock.patch.object(
            cache_mod, "jira_search", return_value=[jira_issue("FAX-1", "Child")]
        ):
            output = self.capture(
                cache_mod.policy,
                self.args(
                    add_epic="FAX-2782",
                    remove_epic=None,
                    allow_epic=None,
                    dry_run=True,
                    filter_id="21689",
                ),
            )
        self.assertEqual(output["suppressed_keys"], ["FAX-1"])
        self.assertFalse((self.cache.parent / "policy.json").exists())

    def test_changed_and_removed_items_invalidate_only_needed_stages(self) -> None:
        first = jira_issue("FAX-1", "Original")
        removed = jira_issue("FAX-2", "Will leave")
        self.sync([first, removed])
        self.record("summary", [summary_record("FAX-1"), summary_record("FAX-2")])

        changed = jira_issue("FAX-1", "Changed")
        result = self.sync([changed], delta=[changed])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["removed_count"], 1)
        cache = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertFalse(cache["items"]["FAX-2"]["active"])
        self.assertNotIn("summary", cache["items"]["FAX-1"]["stages"])

    def test_full_pipeline_returns_qualified_recommendation(self) -> None:
        issue = jira_issue("FAX-1", "Clear work")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.fetch_issue(issue)
        self.record("ticket", [{"key": "FAX-1", "analysis": full_analysis()}])
        repo, sha = self.make_repo()
        self.record(
            "code", [{"key": "FAX-1", "analysis": self.code_analysis(repo, sha)}]
        )

        result = self.capture(cache_mod.recommendations, self.args())
        self.assertEqual([item["key"] for item in result["recommendations"]], ["FAX-1"])
        self.assertEqual(result["recommendations"][0]["complexity"], 2)
        self.assertEqual(result["refinement_opportunities"], [])
        brief = self.capture(cache_mod.recommendations, self.args(brief=True))
        self.assertEqual(brief["recommendations"][0]["key"], "FAX-1")
        self.assertNotIn("evidence", brief["recommendations"][0])

    def test_refinable_gate_is_reported_but_not_recommended(self) -> None:
        issue = jira_issue("FAX-1", "Nearly ready")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.fetch_issue(issue)
        gate = {
            "code": "unresolved_decision",
            "reason": "Empty-state behavior is missing",
            "refinable": True,
        }
        ticket_analysis = full_analysis(gates=[gate])
        ticket_analysis["assessment_confidence"] = 3
        ticket_analysis["refinement_confidence"] = 3
        ticket_analysis["refinement_questions"] = ["What should the empty state show?"]
        self.record("ticket", [{"key": "FAX-1", "analysis": ticket_analysis}])
        repo, sha = self.make_repo()
        code = self.code_analysis(repo, sha, gates=[gate])
        code["assessment_confidence"] = 3
        code["refinement_confidence"] = 3
        code["refinement_questions"] = ["What should the empty state show?"]
        self.record("code", [{"key": "FAX-1", "analysis": code}])

        result = self.capture(cache_mod.recommendations, self.args())
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(
            [item["key"] for item in result["refinement_opportunities"]], ["FAX-1"]
        )

    def test_issue_flattens_adf_bodies_without_caching_them(self) -> None:
        issue = jira_issue("FAX-1", "ADF issue")
        issue["fields"]["description"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "First"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Second"}],
                },
            ],
        }
        issue["fields"]["comment"] = {
            "comments": [
                {
                    "created": "2026-10-01",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "A comment"}],
                            }
                        ],
                    },
                    "author": {"displayName": "Do not retain"},
                }
            ]
        }
        self.sync([issue])
        output = self.fetch_issue(issue)
        self.assertEqual(output["fields"]["description"], "First\nSecond")
        self.assertEqual(output["fields"]["comments"][0]["body"], "A comment")
        self.assertNotIn("author", output["fields"]["comments"][0])

    def test_issue_redacts_common_identifiers_and_splits_blocking_links(self) -> None:
        issue = jira_issue("FAX-1", "Sensitive issue")
        issue["fields"]["description"] = (
            "Email jane@example.com; phone 415-555-1212; internal user id: abc_123."
        )
        issue["fields"]["issuelinks"] = [
            {
                "type": {"outward": "blocks", "inward": "is blocked by"},
                "outwardIssue": {
                    "key": "FAX-2",
                    "fields": {"summary": "Dependent", "status": {"name": "Open"}},
                },
            },
            {
                "type": {"outward": "blocks", "inward": "is blocked by"},
                "inwardIssue": {
                    "key": "FAX-3",
                    "fields": {"summary": "Prerequisite", "status": {"name": "Open"}},
                },
            },
        ]
        self.sync([issue])
        output = self.fetch_issue(issue)
        description = output["fields"]["description"]
        self.assertNotIn("jane@example.com", description)
        self.assertNotIn("415-555-1212", description)
        self.assertIn("[REDACTED_ID]", description)
        self.assertEqual(output["fields"]["redactions"], ["email", "phone", "user_id"])
        self.assertEqual(output["fields"]["links"]["blocks"][0]["key"], "FAX-2")
        self.assertEqual(output["fields"]["links"]["blocked_by"][0]["key"], "FAX-3")

    def test_issue_remembers_parent_epic_without_parent_text(self) -> None:
        issue = jira_issue("FAX-1", "Child", parent_key="FAX-2782")
        self.sync([issue])
        self.fetch_issue(issue)
        status = self.capture(cache_mod.status, self.args())
        observed = status["unclassified_observed_epics"]
        self.assertEqual(observed[0]["key"], "FAX-2782")
        self.assertEqual(observed[0]["seen_count"], 1)
        allowed = self.capture(
            cache_mod.policy,
            self.args(add_epic=None, remove_epic=None, allow_epic="FAX-2782"),
        )
        self.assertEqual(allowed["included_epics"], ["FAX-2782"])

    def test_exact_branch_matching_avoids_jira_key_substrings(self) -> None:
        repo, sha = self.make_repo()
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "update-ref",
                "refs/remotes/origin/fax-29-fix",
                sha,
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "update-ref",
                "refs/remotes/origin/fax-290-fix",
                sha,
            ],
            check=True,
        )
        self.assertEqual(
            cache_mod.matching_branch_heads(repo, "FAX-29"),
            {"local": [], "remote": ["fax-29-fix"]},
        )

    def test_repo_ref_change_stales_code_stage(self) -> None:
        issue = jira_issue("FAX-1", "Clear work")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.fetch_issue(issue)
        self.record("ticket", [{"key": "FAX-1", "analysis": full_analysis()}])
        repo, sha = self.make_repo()
        self.record(
            "code", [{"key": "FAX-1", "analysis": self.code_analysis(repo, sha)}]
        )

        # Create a second commit and move the remote ref to it.
        (repo / "file.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "second"], check=True)
        new_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "update-ref",
                "refs/remotes/origin/master",
                new_sha,
            ],
            check=True,
        )

        result = self.capture(cache_mod.recommendations, self.args())
        self.assertEqual(result["recommendations"], [])
        queued = self.capture(cache_mod.queue, self.args(stage="code", limit=6))
        self.assertEqual([item["key"] for item in queued["candidates"]], ["FAX-1"])

    def test_rubric_change_invalidates_every_stage_without_deleting_records(
        self,
    ) -> None:
        issue = jira_issue("FAX-1", "Clear work")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.rubric.write_text("rubric v2\n", encoding="utf-8")
        status_result = self.capture(cache_mod.status, self.args())
        self.assertEqual(status_result["counts"]["summary_fresh"], 0)
        cache = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertIn("summary", cache["items"]["FAX-1"]["stages"])

    def test_status_and_record_explain_rubric_staleness(self) -> None:
        issue = jira_issue("FAX-1", "Clear work")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.rubric.write_text("rubric v2\n", encoding="utf-8")
        status = self.capture(cache_mod.status, self.args())
        self.assertTrue(status["rubric"]["changed"])
        self.assertEqual(status["stale_reasons"]["scoring rubric changed"], 1)
        with self.assertRaisesRegex(cache_mod.CacheError, "scoring rubric changed"):
            self.record("ticket", [{"key": "FAX-1", "analysis": full_analysis()}])

    def test_corrupt_cache_is_preserved_and_rebuilt(self) -> None:
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("not json", encoding="utf-8")
        result = self.capture(cache_mod.status, self.args())
        self.assertEqual(result["counts"]["total"], 0)
        backups = list(self.cache.parent.glob("cache.json.corrupt-*"))
        self.assertEqual(len(backups), 1)

    def test_clear_requires_confirmation_and_deletes_cache(self) -> None:
        self.sync([jira_issue("FAX-1", "Clear me")])
        with self.assertRaisesRegex(cache_mod.CacheError, "without --yes"):
            cache_mod.clear(self.args(yes=False))
        self.capture(cache_mod.clear, self.args(yes=True))
        self.assertFalse(self.cache.exists())

    def test_code_validation_requires_gate_for_active_pr(self) -> None:
        repo, sha = self.make_repo()
        analysis = self.code_analysis(repo, sha)
        analysis["active_prs"] = ["https://github.com/doximity/example/pull/1"]
        with self.assertRaisesRegex(cache_mod.CacheError, "active_prs requires"):
            cache_mod.validate_analysis(analysis, "code")

    def test_needs_code_spike_is_reported_separately(self) -> None:
        issue = jira_issue("FAX-1", "Hidden implementation")
        self.sync([issue])
        self.record("summary", [summary_record("FAX-1")])
        self.fetch_issue(issue)
        self.record("ticket", [{"key": "FAX-1", "analysis": full_analysis()}])
        repo, sha = self.make_repo()
        gate = {
            "code": "needs_code_spike",
            "reason": "Implementation is in an unavailable package",
            "refinable": False,
        }
        self.record(
            "code",
            [{"key": "FAX-1", "analysis": self.code_analysis(repo, sha, gates=[gate])}],
        )
        result = self.capture(cache_mod.recommendations, self.args())
        self.assertEqual(
            [item["key"] for item in result["needs_code_spike"]], ["FAX-1"]
        )
        self.assertEqual(result["near_misses"], [])


if __name__ == "__main__":
    unittest.main()
