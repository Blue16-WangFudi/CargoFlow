from __future__ import annotations

import unittest

from scripts.github.approval_gate import should_merge
from scripts.github.complete_lark_task import _field_contains_pr


class ApprovalGateTests(unittest.TestCase):
    def test_allows_submitted_approved_review(self) -> None:
        self.assertTrue(
            should_merge(
                {
                    "action": "submitted",
                    "review": {"state": "approved"},
                    "pull_request": {"number": 16},
                }
            )
        )

    def test_allows_exact_merge_approval_comment_on_pr(self) -> None:
        self.assertTrue(
            should_merge(
                {
                    "action": "created",
                    "issue": {"pull_request": {"url": "https://api.github.com/pr/16"}},
                    "comment": {"body": " 同意合并\n"},
                }
            )
        )

    def test_rejects_non_pr_comment_and_non_approval_review(self) -> None:
        self.assertFalse(
            should_merge(
                {
                    "action": "created",
                    "issue": {},
                    "comment": {"body": "同意合并"},
                }
            )
        )
        self.assertFalse(
            should_merge(
                {
                    "action": "submitted",
                    "review": {"state": "commented"},
                    "pull_request": {"number": 16},
                }
            )
        )


class LarkTaskCompletionTests(unittest.TestCase):
    def test_matches_plain_or_markdown_pr_url_fields(self) -> None:
        pr_url = "https://github.com/Blue16-WangFudi/CargoFlow/pull/16"

        self.assertTrue(
            _field_contains_pr(
                {"任务链接": f"[{pr_url}]({pr_url})"},
                pr_url,
            )
        )
        self.assertTrue(
            _field_contains_pr(
                {"验收标准": f"CI pass; PR: {pr_url}/"},
                pr_url,
            )
        )

    def test_rejects_unrelated_pr_url(self) -> None:
        self.assertFalse(
            _field_contains_pr(
                {"任务链接": "https://github.com/Blue16-WangFudi/CargoFlow/pull/15"},
                "https://github.com/Blue16-WangFudi/CargoFlow/pull/16",
            )
        )


if __name__ == "__main__":
    unittest.main()
