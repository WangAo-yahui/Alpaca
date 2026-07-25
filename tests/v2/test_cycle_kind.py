from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.exceptions import StateValidationError
from v2.models.state import CoarseStatus, CycleKind
from v2.runtime import build_daily_paths
from v2.models.state import new_daily_state
from v2.state_machine import (
    CycleKindInputs,
    decide_cycle_kind,
)


class CycleKindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        paths = build_daily_paths(
            "2026-07-23",
            project_root=Path(self.temporary.name),
        )
        self.state = new_daily_state(paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_first_run_is_daily_full(self) -> None:
        self.assertEqual(
            decide_cycle_kind(
                self.state,
                CycleKindInputs(),
            ),
            CycleKind.DAILY_FULL,
        )

    def test_reusable_same_day_state_defaults_to_execution(
        self,
    ) -> None:
        self.state.cycle_ids.append(
            "20260723T090000"
        )
        self.state.first_successful_cycle_id = (
            "20260723T090000"
        )
        self.state.detailed_report_created = True
        self.state.coarse_status = CoarseStatus.VALID
        self.assertEqual(
            decide_cycle_kind(
                self.state,
                CycleKindInputs(),
            ),
            CycleKind.EXECUTION_REFRESH,
        )

    def test_explicit_modes(self) -> None:
        cases = (
            (
                CycleKindInputs(force_full=True),
                CycleKind.DAILY_FULL,
            ),
            (
                CycleKindInputs(
                    force_rebalance=True
                ),
                CycleKind.INTRADAY_REBALANCE,
            ),
            (
                CycleKindInputs(
                    execution_only=True
                ),
                CycleKind.EXECUTION_REFRESH,
            ),
            (
                CycleKindInputs(
                    maintenance_only=True
                ),
                CycleKind.MAINTENANCE_ONLY,
            ),
        )
        for inputs, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    decide_cycle_kind(
                        self.state,
                        inputs,
                    ),
                    expected,
                )

    def test_conflicting_modes_fail_closed(self) -> None:
        with self.assertRaises(
            StateValidationError
        ):
            decide_cycle_kind(
                self.state,
                CycleKindInputs(
                    force_full=True,
                    force_rebalance=True,
                ),
            )


if __name__ == "__main__":
    unittest.main()
