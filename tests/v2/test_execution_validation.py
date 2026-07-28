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
from v2.stages.execution import (
    _automatic_crypto_liquidation_output,
    _defer_without_trade_permission,
    _neutralize_non_executable_intents,
)
from tests.v2.support import (
    stage_e_fixture,
    valid_execution_output,
    valid_protection_plan,
)


class ExecutionValidationTests(unittest.TestCase):
    def test_filled_open_may_be_neutral_hold_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            symbol = source["portfolio"][
                "decisions"
            ][0]["symbol"]
            source["execution_snapshot"][
                "positions"
            ].append(
                {
                    "symbol": symbol,
                    "side": "long",
                    "quantity": "0.123081",
                    "available_quantity": "0.123081",
                    "average_entry_price": "396.68",
                    "current_price": "397.00",
                    "market_value": "48.86",
                }
            )
            baseline = valid_execution_output(
                source
            )
            neutral = copy.deepcopy(baseline)
            decision = neutral["decisions"][0]
            decision.update(
                {
                    "portfolio_action": "hold",
                    "execution_decision": (
                        "no_action"
                    ),
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
            for plan in neutral[
                "protection_plans"
            ]:
                if plan["symbol"] == symbol:
                    plan["apply_to"] = (
                        "existing_position"
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
            validation = validate_execution_output(
                neutral,
                input_payload=source,
                schema=schema,
            )
            self.assertTrue(
                validation.valid,
                validation.errors,
            )

            executable_mismatch = copy.deepcopy(
                baseline
            )
            executable_mismatch["decisions"][0][
                "portfolio_action"
            ] = "hold"
            codes = {
                item["code"]
                for item in validate_execution_output(
                    executable_mismatch,
                    input_payload=source,
                    schema=schema,
                ).errors
            }
            self.assertIn(
                "PORTFOLIO_ACTION_MISMATCH",
                codes,
            )

    def test_none_protection_has_no_fractional_tif_requirement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            source["execution_policy"][
                "position_protection"
            ]["allow_none_when_thesis_based"] = True
            source["execution_snapshot"][
                "positions"
            ][0]["quantity"] = "0.5"
            payload = valid_execution_output(source)
            plan = payload["protection_plans"][0]
            plan.update(
                {
                    "mode": "none",
                    "coverage_fraction": "0",
                    "time_in_force": "gtc",
                    "take_profit_price": None,
                    "stop_price": None,
                    "stop_limit_price": None,
                    "trail_price": None,
                    "trail_percent": None,
                    "stages": [],
                }
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
            validation = validate_execution_output(
                payload,
                input_payload=source,
                schema=schema,
            )
            self.assertNotIn(
                "FRACTIONAL_PROTECTION_TIF_INVALID",
                {
                    item["code"]
                    for item in validation.errors
                },
            )

    def test_dry_run_model_approval_is_safely_deferred(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            source["trade_permission"][
                "submission_enabled"
            ] = False
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            model_source = copy.deepcopy(source)
            model_source["trade_permission"][
                "submission_enabled"
            ] = True
            payload = valid_execution_output(
                model_source
            )
            self.assertTrue(
                any(
                    item["execution_decision"]
                    == "approve"
                    for item in payload["decisions"]
                )
            )
            deferred, symbols = (
                _defer_without_trade_permission(
                    payload,
                    input_payload=source,
                )
            )
            normalized, _ = (
                _neutralize_non_executable_intents(
                    deferred
                )
            )
            self.assertTrue(symbols)
            self.assertTrue(
                all(
                    item["execution_decision"]
                    == "defer"
                    for item in normalized[
                        "decisions"
                    ]
                )
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
                    normalized,
                    input_payload=source,
                    schema=schema,
                ).valid
            )

    def test_live_crypto_is_forced_to_market_close(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            source["profile"]["environment"] = "live"
            symbol = source["portfolio"][
                "decisions"
            ][0]["symbol"]
            source["portfolio"]["decisions"][0][
                "current_position"
            ] = True
            source["execution_snapshot"][
                "assets"
            ][symbol]["asset_class"] = "crypto"
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            source["execution_snapshot"][
                "quotes"
            ][symbol]["quote_age_seconds"] = "7200"
            source["execution_snapshot"][
                "positions"
            ].append(
                {
                    "symbol": symbol,
                    "side": "long",
                    "quantity": "2",
                    "available_quantity": "2",
                    "average_entry_price": "100",
                    "current_price": "99",
                    "market_value": "198",
                }
            )
            automatic = (
                _automatic_crypto_liquidation_output(
                    source
                )
            )
            self.assertIsNotNone(automatic)
            assert automatic is not None
            forced, symbols = automatic
            self.assertEqual(
                forced["network_research"][
                    "status"
                ],
                "not_requested",
            )
            self.assertEqual(symbols, (symbol,))
            decision = forced["decisions"][0]
            self.assertEqual(
                decision["execution_decision"],
                "approve",
            )
            self.assertEqual(
                decision["portfolio_action"],
                "close",
            )
            self.assertEqual(
                decision["side"],
                "sell",
            )
            self.assertEqual(
                decision["execution_fraction"],
                "1",
            )
            self.assertEqual(
                decision["order_intent"][
                    "preferred_type"
                ],
                "market",
            )
            self.assertEqual(
                decision["order_intent"][
                    "time_in_force_preference"
                ],
                "gtc",
            )
            for equity_decision in forced[
                "decisions"
            ][1:]:
                self.assertEqual(
                    equity_decision[
                        "execution_decision"
                    ],
                    "defer",
                )
                self.assertEqual(
                    equity_decision["side"],
                    "none",
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
            validation = validate_execution_output(
                forced,
                input_payload=source,
                schema=schema,
            )
            self.assertTrue(
                validation.valid,
                validation.errors,
            )

    def test_existing_crypto_position_cannot_increase(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            symbol = source["portfolio"][
                "decisions"
            ][0]["symbol"]
            source["portfolio"]["decisions"][0].update(
                {
                    "current_position": True,
                    "action": "increase",
                }
            )
            source["execution_snapshot"][
                "assets"
            ][symbol]["asset_class"] = "crypto"
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            source["execution_snapshot"][
                "positions"
            ].append(
                {
                    "symbol": symbol,
                    "side": "long",
                    "quantity": "1",
                    "available_quantity": "1",
                    "market_value": "100",
                }
            )
            payload = valid_execution_output(source)
            decision = payload["decisions"][0]
            decision.update(
                {
                    "portfolio_action": "increase",
                    "side": "buy",
                }
            )
            decision["order_intent"].update(
                {
                    "time_in_force_preference": "gtc",
                    "extended_hours_requested": False,
                }
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
            codes = {
                item["code"]
                for item in validate_execution_output(
                    payload,
                    input_payload=source,
                    schema=schema,
                ).errors
            }
            self.assertIn(
                "CRYPTO_POSITION_EXPANSION_FORBIDDEN",
                codes,
            )

    def test_live_closed_session_conservative_queue(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution.input_result.payload
            )
            source["profile"]["environment"] = "live"
            source["execution_snapshot"][
                "market_phase"
            ] = "market_closed_weekend"
            source["risk_profile"][
                "execution_limits"
            ][
                "max_closed_session_quote_age_seconds"
            ] = "345600"
            for quote in source[
                "execution_snapshot"
            ]["quotes"].values():
                quote[
                    "quote_age_seconds"
                ] = "172800"
            payload = valid_execution_output(source)
            payload["market_assessment"][
                "market_phase"
            ] = "market_closed_weekend"
            for decision in payload["decisions"]:
                decision[
                    "execution_decision"
                ] = "approve"
                decision["side"] = "buy"
                decision[
                    "execution_fraction"
                ] = "0.25"
                decision["order_intent"] = {
                    "preferred_type": "limit",
                    "time_in_force_preference": "day",
                    "extended_hours_requested": False,
                    "allow_queue": True,
                    "allow_partial_fill": True,
                }
                symbol = decision["symbol"]
                decision["price_condition"] = {
                    "reference": "ask",
                    "limit_price": str(
                        source[
                            "execution_snapshot"
                        ]["quotes"][symbol][
                            "ask_price"
                        ]
                    ),
                    "do_not_execute_above": None,
                    "review_below": None,
                }
            planned = {
                item["symbol"]
                for item in payload[
                    "protection_plans"
                ]
            }
            for decision in payload["decisions"]:
                symbol = decision["symbol"]
                if symbol in planned:
                    continue
                reference = float(
                    source[
                        "execution_snapshot"
                    ]["quotes"][symbol][
                        "ask_price"
                    ]
                )
                payload["protection_plans"].append(
                    valid_protection_plan(
                        symbol,
                        reference=reference,
                        apply_to="new_entry",
                    )
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
            validation = validate_execution_output(
                payload,
                input_payload=source,
                schema=schema,
            )
            self.assertTrue(
                validation.valid,
                validation.errors,
            )

    def test_non_executable_intent_is_safely_neutralized(
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
            ] = "overnight_session"
            payload = valid_execution_output(
                source
            )
            for decision in payload["decisions"]:
                decision.update(
                    {
                        "execution_decision": "defer",
                        "side": "buy",
                        "execution_fraction": "0",
                        "urgency": "normal",
                    }
                )
                decision["price_condition"][
                    "reference"
                ] = "ask"
                decision["price_condition"][
                    "limit_price"
                ] = "100"
                decision["order_intent"].update(
                    {
                        "preferred_type": "limit",
                        "time_in_force_preference": (
                            "day"
                        ),
                    }
                )
            normalized, symbols = (
                _neutralize_non_executable_intents(
                    payload
                )
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
        self.assertTrue(symbols)
        self.assertTrue(
            validate_execution_output(
                normalized,
                input_payload=source,
                schema=schema,
            ).valid
        )
        for decision in normalized["decisions"]:
            self.assertEqual(
                decision["side"],
                "none",
            )
            self.assertEqual(
                decision["order_intent"][
                    "preferred_type"
                ],
                "none",
            )

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
            existing_symbols = {
                item["symbol"]
                for item in source[
                    "execution_snapshot"
                ]["positions"]
                if source[
                    "execution_snapshot"
                ]["assets"].get(
                    item["symbol"],
                    {},
                ).get("asset_class")
                != "crypto"
            }
            rejected["protection_plans"] = [
                item
                for item in rejected[
                    "protection_plans"
                ]
                if item["symbol"]
                in existing_symbols
            ]
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
