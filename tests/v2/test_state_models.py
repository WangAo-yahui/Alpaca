from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from v2.models.state import (
    CycleKind,
    CycleState,
    CycleStatus,
    DailyState,
    ReviewMode,
    StepName,
    complete_cycle,
    new_cycle_state,
    new_daily_state,
    save_cycle_state,
    save_daily_state,
)
from v2.runtime import (
    build_cycle_paths,
    build_daily_paths,
    load_json_object,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StateModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.daily_paths = build_daily_paths(
            "2026-07-23",
            project_root=self.root,
        )
        self.cycle_paths = build_cycle_paths(
            run_date="2026-07-23",
            cycle_id="20260723T120502",
            project_root=self.root,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_daily_round_trip_and_schema(self) -> None:
        state = new_daily_state(
            self.daily_paths,
            config_version="test-v1",
            config_signature="abc123",
        )
        save_daily_state(
            self.daily_paths.daily_state,
            state,
        )
        payload = load_json_object(
            self.daily_paths.daily_state
        )
        restored = DailyState.from_dict(payload)
        self.assertEqual(
            restored.to_dict(),
            payload,
        )
        self._validate_schema(
            "daily_state.schema.json",
            payload,
        )

    def test_cycle_round_trip_and_schema(self) -> None:
        state = new_cycle_state(
            self.cycle_paths,
            cycle_kind=CycleKind.DAILY_FULL,
            review_mode=ReviewMode.PROMPT,
            config_version="test-v1",
            config_signature="abc123",
        )
        save_cycle_state(
            self.cycle_paths.cycle_state,
            state,
        )
        payload = load_json_object(
            self.cycle_paths.cycle_state
        )
        restored = CycleState.from_dict(payload)
        self.assertEqual(
            restored.to_dict(),
            payload,
        )
        self._validate_schema(
            "cycle_state.schema.json",
            payload,
        )

    def test_duplicate_completed_step_is_invalid(
        self,
    ) -> None:
        state = new_cycle_state(
            self.cycle_paths,
            cycle_kind=CycleKind.DAILY_FULL,
            review_mode=ReviewMode.PROMPT,
        )
        state.completed_steps = [
            StepName.MAINTAIN_PREVIOUS,
            StepName.MAINTAIN_PREVIOUS,
        ]
        with self.assertRaises(ValueError):
            state.validate()

    def test_terminal_state_has_completion_time(
        self,
    ) -> None:
        state = new_cycle_state(
            self.cycle_paths,
            cycle_kind=CycleKind.MAINTENANCE_ONLY,
            review_mode=ReviewMode.SKIPPED_BY_FLAG,
        )
        complete_cycle(
            state,
            status=CycleStatus.COMPLETED_NO_ACTION,
            stop_reason="maintenance complete",
        )
        self.assertEqual(
            state.current_step,
            StepName.COMPLETE,
        )
        self.assertIsNotNone(state.completed_at)
        self.assertFalse(state.resume_allowed)
        state.validate()

    def _validate_schema(
        self,
        file_name: str,
        payload: dict[str, object],
    ) -> None:
        schema = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "v2"
                / file_name
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(
            schema,
            format_checker=(
                Draft202012Validator.FORMAT_CHECKER
            ),
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: list(item.path),
        )
        self.assertEqual(
            [error.message for error in errors],
            [],
        )


if __name__ == "__main__":
    unittest.main()
