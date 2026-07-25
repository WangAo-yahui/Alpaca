"""覆盖 Stage E 身份、范围、权重、比例、行情与禁止字段校验。

作用：从一个合法 regular-session 输出逐项注入业务违规。
重要性：Codex 的 Schema 合法输出仍必须经过 Python 风控。
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.models.execution import (
    validate_execution_output,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    stage_e_fixture,
    valid_execution_output,
)


class ExecutionValidationTests(unittest.TestCase):
    def test_core_business_rejections(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution
                .input_result.payload
            )
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            baseline = valid_execution_output(
                source
            )
            release = load_strategy_release(
                "core_long",
                "1.2.0",
                project_root=root,
            )
            schema = load_json_object(
                release.root
                / "schemas/execution_output.schema.json"
            )
            self.assertTrue(
                validate_execution_output(
                    baseline,
                    input_payload=source,
                    schema=schema,
                ).valid
            )
            cases = {}
            outside = copy.deepcopy(baseline)
            outside["decisions"][0][
                "symbol"
            ] = "OUTSIDE"
            cases[
                "SYMBOL_OUTSIDE_PORTFOLIO"
            ] = outside
            weight = copy.deepcopy(baseline)
            weight["decisions"][0][
                "target_weight"
            ] = "0.50"
            cases[
                "WEIGHT_ADJUSTMENT_REQUIRES_REPLAN"
            ] = weight
            fraction = copy.deepcopy(baseline)
            fraction["decisions"][0][
                "execution_fraction"
            ] = "1.10"
            cases[
                "EXECUTION_FRACTION_OUT_OF_RANGE"
            ] = fraction
            forbidden = copy.deepcopy(baseline)
            forbidden["decisions"][0][
                "quantity"
            ] = "1"
            cases[
                "FORBIDDEN_EXECUTION_FIELD"
            ] = forbidden
            bad_price = copy.deepcopy(baseline)
            bad_price["decisions"][0][
                "price_condition"
            ]["do_not_execute_above"] = "90"
            cases[
                "PRICE_CONDITION_INCONSISTENT"
            ] = bad_price
            unknown_source = copy.deepcopy(source)
            unknown_source[
                "execution_snapshot"
            ]["market_phase"] = "unknown"
            unknown = copy.deepcopy(baseline)
            unknown["market_assessment"][
                "market_phase"
            ] = "unknown"
            cases[
                "UNKNOWN_PHASE_CANNOT_APPROVE"
            ] = (unknown, unknown_source)
            coarse_source = copy.deepcopy(source)
            coarse_source["portfolio"][
                "decisions"
            ][0]["in_current_coarse"] = False
            cases[
                "NEW_POSITION_NOT_IN_COARSE"
            ] = (baseline, coarse_source)
            stale_source = copy.deepcopy(source)
            symbol = baseline["decisions"][0][
                "symbol"
            ]
            stale_source[
                "execution_snapshot"
            ]["quotes"][symbol][
                "quote_age_seconds"
            ] = 999
            cases["QUOTE_STALE_OR_MISSING"] = (
                baseline,
                stale_source,
            )
            spread_source = copy.deepcopy(source)
            spread_source[
                "execution_snapshot"
            ]["quotes"][symbol][
                "spread_bps"
            ] = 999
            cases["SPREAD_LIMIT_BREACHED"] = (
                baseline,
                spread_source,
            )
            for code, value in cases.items():
                payload, input_value = (
                    value
                    if isinstance(value, tuple)
                    else (value, source)
                )
                with self.subTest(code=code):
                    codes = {
                        item["code"]
                        for item in (
                            validate_execution_output(
                                payload,
                                input_payload=(
                                    input_value
                                ),
                                schema=schema,
                            ).errors
                        )
                    }
                    self.assertIn(code, codes)

            rejected = copy.deepcopy(baseline)
            rejected["review_response"][
                "rejected_requests"
            ] = [
                "Initial guidance buy request rejected"
            ]
            for decision in rejected["decisions"]:
                decision.update(
                    {
                        "execution_decision": "reject",
                        "side": "none",
                        "execution_fraction": "0",
                        "urgency": "none",
                    }
                )
                decision["price_condition"] = {
                    "reference": "none",
                    "limit_price": None,
                    "do_not_execute_above": None,
                    "review_below": None,
                }
                decision["order_intent"] = {
                    "preferred_type": "none",
                    "time_in_force_preference": "none",
                    "extended_hours_requested": False,
                    "allow_queue": False,
                    "allow_partial_fill": False,
                }
            self.assertTrue(
                validate_execution_output(
                    rejected,
                    input_payload=source,
                    schema=schema,
                ).valid
            )

            modified = copy.deepcopy(baseline)
            first = modified["decisions"][0]
            original = float(
                first["target_weight"]
            )
            first["execution_decision"] = "modify"
            first["target_weight"] = str(
                max(0, original - 0.005)
            )
            self.assertTrue(
                validate_execution_output(
                    modified,
                    input_payload=source,
                    schema=schema,
                ).valid
            )


if __name__ == "__main__":
    unittest.main()
