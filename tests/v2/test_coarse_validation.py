from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.codex.validation import (
    load_coarse_schema,
    validate_coarse_output,
)
from v2.config import load_config
from v2.models.coarse import build_coarse_input
from tests.v2.support import (
    prepare_stage_c_project,
    valid_coarse_output,
)


class CoarseValidationTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
    ) -> tuple[dict, dict, dict]:
        prepare_stage_c_project(root)
        input_payload = build_coarse_input(
            config=load_config(
                project_root=root
            ),
            run_date="2026-07-23",
            base_snapshot={
                "market_phase": "regular_session",
                "positions": [],
                "open_orders": [],
                "assets": [],
            },
        ).payload
        output = valid_coarse_output(
            input_payload
        )
        schema = load_coarse_schema(
            root
            / "schemas/v2/coarse_output.schema.json"
        )
        return input_payload, output, schema

    def test_valid_exact_sixty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            input_payload, output, schema = (
                self._fixture(Path(temp))
            )
            result = validate_coarse_output(
                output,
                input_payload=input_payload,
                schema=schema,
            )
            self.assertTrue(result.valid)
            self.assertEqual(result.errors, ())

    def test_rejects_duplicate_missing_required_and_forbidden(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            input_payload, output, schema = (
                self._fixture(Path(temp))
            )
            broken = copy.deepcopy(output)
            broken["selections"][0][
                "symbol"
            ] = broken["selections"][1]["symbol"]
            broken["selections"][0][
                "target_weight"
            ] = 0.1
            result = validate_coarse_output(
                broken,
                input_payload=input_payload,
                schema=schema,
            )
            codes = {
                item["code"]
                for item in result.errors
            }
            self.assertFalse(result.valid)
            self.assertIn(
                "DUPLICATE_SELECTION_SYMBOL",
                codes,
            )
            self.assertIn(
                "MUST_INCLUDE_MISSING",
                codes,
            )
            self.assertIn(
                "FORBIDDEN_OUTPUT_FIELD",
                codes,
            )

    def test_held_omission_is_warning_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            input_payload = build_coarse_input(
                config=load_config(
                    project_root=root
                ),
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "regular_session",
                    "positions": [
                        {"symbol": "S064"}
                    ],
                    "open_orders": [],
                    "assets": [],
                },
            ).payload
            output = valid_coarse_output(
                input_payload
            )
            schema = load_coarse_schema(
                root
                / "schemas/v2/coarse_output.schema.json"
            )
            result = validate_coarse_output(
                output,
                input_payload=input_payload,
                schema=schema,
            )
            self.assertTrue(result.valid)
            self.assertIn(
                "HELD_SYMBOL_NOT_SELECTED",
                {
                    item["code"]
                    for item in result.warnings
                },
            )

    def test_all_core_business_rejections(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            input_payload, output, schema = (
                self._fixture(Path(temp))
            )
            cases = []
            too_few = copy.deepcopy(output)
            too_few["selections"].pop()
            too_few["selection_count"] = 59
            cases.append(
                (
                    too_few,
                    "SELECTION_LIST_NOT_60",
                    input_payload,
                )
            )
            too_many = copy.deepcopy(output)
            too_many["selections"].append(
                copy.deepcopy(
                    too_many["selections"][-1]
                )
            )
            too_many["selection_count"] = 61
            cases.append(
                (
                    too_many,
                    "SELECTION_LIST_NOT_60",
                    input_payload,
                )
            )
            outside = copy.deepcopy(output)
            outside["selections"][-1][
                "symbol"
            ] = "ZZZZ"
            cases.append(
                (
                    outside,
                    "SYMBOL_NOT_ELIGIBLE_INPUT",
                    input_payload,
                )
            )
            rank = copy.deepcopy(output)
            rank["selections"][-1]["rank"] = 1
            cases.append(
                (
                    rank,
                    "INVALID_SELECTION_RANKS",
                    input_payload,
                )
            )
            signature = copy.deepcopy(output)
            signature["input_signature"] = "0" * 64
            cases.append(
                (
                    signature,
                    "INPUT_SIGNATURE_MISMATCH",
                    input_payload,
                )
            )
            wrong_date = copy.deepcopy(output)
            wrong_date["run_date"] = "2026-07-22"
            cases.append(
                (
                    wrong_date,
                    "RUN_DATE_MISMATCH",
                    input_payload,
                )
            )
            excluded_input = copy.deepcopy(
                input_payload
            )
            excluded_symbol = output[
                "selections"
            ][-1]["symbol"]
            excluded_input["exclusions"] = [
                excluded_symbol
            ]
            cases.append(
                (
                    copy.deepcopy(output),
                    "EXCLUDED_SYMBOL_SELECTED",
                    excluded_input,
                )
            )
            for broken, expected, case_input in cases:
                with self.subTest(expected=expected):
                    result = validate_coarse_output(
                        broken,
                        input_payload=case_input,
                        schema=schema,
                    )
                    self.assertFalse(result.valid)
                    self.assertIn(
                        expected,
                        {
                            item["code"]
                            for item in result.errors
                        },
                    )

    def test_network_success_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            input_payload, output, schema = (
                self._fixture(Path(temp))
            )
            output["status"] = "success"
            output["network_research"] = {
                "status": "completed",
                "web_access": True,
                "summary": "Research completed",
                "warnings": [],
            }
            output["warnings"] = []
            result = validate_coarse_output(
                output,
                input_payload=input_payload,
                schema=schema,
            )
            self.assertTrue(result.valid)


if __name__ == "__main__":
    unittest.main()
