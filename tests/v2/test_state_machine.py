from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.exceptions import (
    ErrorCategory,
    SafetyBlockedError,
    StateValidationError,
    TemporaryDataError,
)
from v2.models.state import (
    CycleKind,
    CycleStatus,
    ReviewMode,
    StageStatus,
    StepName,
    new_cycle_state,
)
from v2.runtime import build_cycle_paths
from v2.state_machine import (
    begin_next_step,
    complete_current_step,
    fail_current_step,
    next_step,
    pause_for_review,
    prepare_state,
    step_plan,
    validate_resume_compatibility,
)


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        paths = build_cycle_paths(
            run_date="2026-07-23",
            cycle_id="20260723T120502",
            project_root=Path(self.temporary.name),
        )
        self.state = new_cycle_state(
            paths,
            cycle_kind=CycleKind.DAILY_FULL,
            review_mode=ReviewMode.PROMPT,
            config_version="test-v1",
            config_signature="abc123",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_daily_full_order_and_completion(
        self,
    ) -> None:
        seen: list[StepName] = []
        while next_step(self.state) is not None:
            step = begin_next_step(self.state)
            seen.append(step)
            complete_current_step(
                self.state,
                final_status=(
                    CycleStatus.COMPLETED_NO_ACTION
                    if step == StepName.COMPLETE
                    else CycleStatus.COMPLETED
                ),
            )

        self.assertEqual(
            tuple(seen),
            step_plan(CycleKind.DAILY_FULL),
        )
        self.assertEqual(
            self.state.status,
            CycleStatus.COMPLETED_NO_ACTION,
        )
        self.assertFalse(self.state.resume_allowed)

    def test_execution_refresh_skips_reused_stages(
        self,
    ) -> None:
        self.state.cycle_kind = (
            CycleKind.EXECUTION_REFRESH
        )
        prepare_state(self.state)
        self.assertEqual(
            self.state.stages["coarse"].status,
            StageStatus.SKIPPED,
        )
        self.assertEqual(
            self.state.stages["portfolio"].status,
            StageStatus.SKIPPED,
        )
        self.assertEqual(
            self.state.stages["review"].status,
            StageStatus.SKIPPED,
        )
        self.assertNotIn(
            StepName.RUN_PORTFOLIO,
            step_plan(
                CycleKind.EXECUTION_REFRESH
            ),
        )

    def test_retryable_failure_resumes_same_step(
        self,
    ) -> None:
        first = begin_next_step(self.state)
        self.assertEqual(
            first,
            StepName.MAINTAIN_PREVIOUS,
        )
        category = fail_current_step(
            self.state,
            TemporaryDataError("temporary"),
        )
        self.assertEqual(
            category,
            ErrorCategory.RETRYABLE,
        )
        self.assertEqual(
            self.state.status,
            CycleStatus.FAILED_RETRIABLE,
        )
        self.assertTrue(self.state.resume_allowed)

        retried = begin_next_step(self.state)
        self.assertEqual(retried, first)
        self.assertEqual(
            self.state.step_attempts[first.value],
            2,
        )

    def test_safety_block_is_terminal_normal_state(
        self,
    ) -> None:
        begin_next_step(self.state)
        category = fail_current_step(
            self.state,
            SafetyBlockedError(
                "market phase blocks order"
            ),
        )
        self.assertEqual(
            category,
            ErrorCategory.SAFETY_BLOCK,
        )
        self.assertEqual(
            self.state.status,
            CycleStatus.BLOCKED,
        )
        self.assertEqual(
            self.state.current_step,
            StepName.COMPLETE,
        )
        self.assertIsNone(next_step(self.state))

    def test_unknown_failure_is_terminal(self) -> None:
        begin_next_step(self.state)
        category = fail_current_step(
            self.state,
            RuntimeError("programming defect"),
        )
        self.assertEqual(category, ErrorCategory.FATAL)
        self.assertEqual(
            self.state.status,
            CycleStatus.FAILED_TERMINAL,
        )

    def test_review_can_pause_and_resume(self) -> None:
        while (
            next_step(self.state)
            != StepName.COLLECT_REVIEW
        ):
            begin_next_step(self.state)
            complete_current_step(self.state)

        begin_next_step(self.state)
        pause_for_review(self.state)
        self.assertEqual(
            self.state.status,
            CycleStatus.WAITING_FOR_REVIEW,
        )
        complete_current_step(self.state)
        self.assertIn(
            StepName.COLLECT_REVIEW,
            self.state.completed_steps,
        )

    def test_config_mismatch_blocks_resume(self) -> None:
        with self.assertRaises(
            StateValidationError
        ):
            validate_resume_compatibility(
                self.state,
                config_version="test-v2",
                config_signature="different",
            )


if __name__ == "__main__":
    unittest.main()
