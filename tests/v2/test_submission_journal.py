"""验证 submission journal 的写前持久化与恢复状态。

作用：检查创建、原子 transition、attempt_count 和 uncertain 标志。
重要性：journal 是中断恢复时避免重复下单的唯一写前证据。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import (
    SubmissionOperationState,
    SubmissionOperationType,
)
from v2.runtime import load_json_object
from tests.v2.submission_support import journal_for, operation


class SubmissionJournalTests(unittest.TestCase):
    def test_create_persists_prepared_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = journal_for(root, operation())
            payload = load_json_object(journal.path)
            self.assertEqual(
                payload["operations"][0]["state"],
                "prepared",
            )

    def test_write_start_is_persisted_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = journal_for(
                Path(temporary), operation()
            )
            journal.persist_before_write(
                "submit-plan-1"
            )
            payload = load_json_object(journal.path)
            self.assertEqual(
                payload["operations"][0]["state"],
                "request_started",
            )
            self.assertEqual(
                payload["operations"][0]["attempt_count"],
                1,
            )

    def test_uncertain_flag_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = journal_for(
                Path(temporary), operation()
            )
            journal.transition(
                "submit-plan-1",
                SubmissionOperationState.UNCERTAIN,
                error={"type": "Timeout", "message": "unknown"},
            )
            loaded = journal.load_or_create(
                journal.path,
                profile_id="paper1",
                run_date="2026-07-24",
                cycle_id="20260724T140000",
            )
            self.assertTrue(loaded.has_uncertain)

    def test_cannot_start_same_operation_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = journal_for(
                Path(temporary), operation()
            )
            journal.persist_before_write(
                "submit-plan-1"
            )
            with self.assertRaises(ValueError):
                journal.persist_before_write(
                    "submit-plan-1"
                )

    def test_replan_replaces_only_unstarted_submits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cancel = operation(
                kind=SubmissionOperationType.CANCEL,
                broker_order_id="broker-1",
            )
            old_submit = operation()
            journal = journal_for(
                Path(temporary),
                cancel,
            )
            journal.operations.append(old_submit)
            journal.save()
            replacement = operation()
            replacement.operation_id = "submit-plan-2"
            replacement.plan_id = "plan-2"
            replacement.client_order_id = "wa2-plan-2"
            journal.replace_unstarted_submissions(
                [replacement]
            )
            self.assertEqual(
                [
                    item.operation_id
                    for item in journal.operations
                ],
                ["cancel-action-1", "submit-plan-2"],
            )

    def test_replan_rejects_started_submit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = journal_for(
                Path(temporary), operation()
            )
            journal.persist_before_write(
                "submit-plan-1"
            )
            with self.assertRaises(ValueError):
                journal.replace_unstarted_submissions(
                    [operation()]
                )


if __name__ == "__main__":
    unittest.main()
