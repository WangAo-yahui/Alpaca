"""覆盖 Stage D 权重、范围、身份、资本与禁止字段业务校验。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v2.main import run_stage_d
from v2.models.portfolio import (
    validate_portfolio_output,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class PortfolioValidationTests(unittest.TestCase):
    def test_emerging_watchlist_weight_limits(
        self,
    ) -> None:
        generated = datetime.now(timezone.utc)
        input_payload = {
            "profile": {"profile_id": "live1"},
            "release": {
                "strategy_id": "core_long",
                "strategy_version": "1.3.0",
            },
            "run_date": "2026-07-29",
            "cycle_id": "20260729T120000",
            "input_signature": "b" * 64,
            "policy": {
                "maximum_sector_weight": "1",
                "minimum_target_weight": "0.01",
                "target_holdings": {
                    "minimum": 0,
                    "maximum": 20,
                },
                "risk_profile": {
                    "maximum_single_position_weight": "1",
                    "minimum_cash_weight": "0",
                },
                "emerging_growth_watchlist": {
                    "source_name": "watchlist_non_sp500",
                    "maximum_initial_target_weight": "0.03",
                    "maximum_aggregate_target_weight": "0.03",
                    "require_staged_entry": True,
                },
            },
            "positions": [],
            "candidates": [
                {
                    "symbol": "EARLY",
                    "sector": "Technology",
                    "source": "watchlist_non_sp500",
                    "screen_new_position_eligible": True,
                }
            ],
            "open_orders": [],
        }
        payload = {
            "stage": "portfolio_decision",
            "profile_id": "live1",
            "strategy_id": "core_long",
            "strategy_version": "1.3.0",
            "run_date": "2026-07-29",
            "cycle_id": "20260729T120000",
            "input_signature": "b" * 64,
            "status": "success",
            "generated_at": generated.isoformat(),
            "valid_until": (
                generated + timedelta(hours=1)
            ).isoformat(),
            "allocation": {
                "target_cash_weight": "0.96",
                "target_invested_weight": "0.04",
                "target_position_count": 1,
                "maximum_single_symbol_weight": "1",
                "maximum_sector_weight": "1",
            },
            "decisions": [
                {
                    "symbol": "EARLY",
                    "current_position": False,
                    "in_current_coarse": True,
                    "action": "open",
                    "target_weight": "0.04",
                    "maximum_weight": "0.04",
                    "valuation": {
                        "status": "no_reliable_estimate",
                        "market_price": None,
                        "value_range_low": None,
                        "value_range_high": None,
                        "margin_of_safety_fraction": None,
                    },
                    "accumulation_plan": {
                        "style": "staged",
                        "planned_total_fraction": "1",
                        "tranches": [],
                    },
                }
            ],
        }
        result = validate_portfolio_output(
            payload,
            input_payload=input_payload,
            schema={"type": "object"},
        )
        self.assertIn(
            "EMERGING_INITIAL_WEIGHT_LIMIT_BREACHED",
            {
                item["code"]
                for item in result.errors
            },
        )
        self.assertIn(
            "EMERGING_AGGREGATE_WEIGHT_LIMIT_BREACHED",
            {
                item["code"]
                for item in result.errors
            },
        )

    def test_unreliable_value_can_keep_known_market_price(
        self,
    ) -> None:
        generated = datetime.now(timezone.utc)
        input_payload = {
            "profile": {"profile_id": "live1"},
            "release": {
                "strategy_id": "core_long",
                "strategy_version": "1.3.0",
            },
            "run_date": "2026-07-28",
            "cycle_id": "20260728T120000",
            "input_signature": "a" * 64,
            "policy": {
                "maximum_sector_weight": "1",
                "target_holdings": {
                    "minimum": 0,
                    "maximum": 20,
                },
                "risk_profile": {
                    "maximum_single_position_weight": "1",
                    "minimum_cash_weight": "0",
                },
            },
            "positions": [],
            "candidates": [
                {
                    "symbol": "SPY",
                    "sector": "ETF",
                    "screen_new_position_eligible": True,
                }
            ],
            "open_orders": [],
        }
        decision = {
            "symbol": "SPY",
            "current_position": False,
            "in_current_coarse": True,
            "action": "watch",
            "target_weight": "0",
            "maximum_weight": "0",
            "valuation": {
                "status": "no_reliable_estimate",
                "market_price": "739.46",
                "value_range_low": None,
                "value_range_high": None,
                "margin_of_safety_fraction": None,
                "evidence_quality": "insufficient",
            },
            "accumulation_plan": {
                "style": "wait",
                "planned_total_fraction": "0",
                "tranches": [],
            },
        }
        payload = {
            "stage": "portfolio_decision",
            "profile_id": "live1",
            "strategy_id": "core_long",
            "strategy_version": "1.3.0",
            "run_date": "2026-07-28",
            "cycle_id": "20260728T120000",
            "input_signature": "a" * 64,
            "status": "success_local_only",
            "generated_at": generated.isoformat(),
            "valid_until": (
                generated + timedelta(hours=1)
            ).isoformat(),
            "allocation": {
                "target_cash_weight": "1",
                "target_invested_weight": "0",
                "target_position_count": 0,
                "maximum_single_symbol_weight": "1",
                "maximum_sector_weight": "1",
            },
            "decisions": [decision],
        }
        accepted = validate_portfolio_output(
            payload,
            input_payload=input_payload,
            schema={"type": "object"},
        )
        accepted_codes = {
            item["code"]
            for item in accepted.errors
        }
        self.assertNotIn(
            "UNRELIABLE_VALUATION_HAS_NUMBERS",
            accepted_codes,
        )

        with_estimated_value = copy.deepcopy(payload)
        with_estimated_value["decisions"][0][
            "valuation"
        ]["value_range_low"] = "700"
        rejected = validate_portfolio_output(
            with_estimated_value,
            input_payload=input_payload,
            schema={"type": "object"},
        )
        self.assertIn(
            "UNRELIABLE_VALUATION_HAS_NUMBERS",
            {
                item["code"]
                for item in rejected.errors
            },
        )

    def test_core_business_rules(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            result = run_stage_d(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=FakePortfolioRunner(),
            )
            assert result.portfolio is not None
            baseline = result.portfolio.output
            input_payload = (
                result.portfolio.input_result.payload
            )
            release = load_strategy_release(
                "core_long",
                "1.1.0",
                project_root=root,
            )
            schema = load_json_object(
                release.root
                / "schemas"
                / "portfolio_output.schema.json"
            )
            valid = validate_portfolio_output(
                baseline,
                input_payload=input_payload,
                schema=schema,
            )
            self.assertTrue(valid.valid)
            self.assertTrue(valid.warnings)

            cases = {}
            cash = copy.deepcopy(baseline)
            cash["allocation"][
                "target_cash_weight"
            ] = "0.70"
            cases["ALLOCATION_SUM_MISMATCH"] = cash

            overweight = copy.deepcopy(baseline)
            overweight["decisions"][0][
                "target_weight"
            ] = "0.09"
            overweight["allocation"][
                "target_invested_weight"
            ] = "0.25"
            overweight["allocation"][
                "target_cash_weight"
            ] = "0.75"
            cases[
                "SINGLE_SYMBOL_LIMIT_BREACHED"
            ] = overweight

            duplicate = copy.deepcopy(baseline)
            duplicate["decisions"][1]["symbol"] = (
                duplicate["decisions"][0]["symbol"]
            )
            cases[
                "DUPLICATE_DECISION_SYMBOL"
            ] = duplicate

            forbidden = copy.deepcopy(baseline)
            forbidden["decisions"][0][
                "quantity"
            ] = "1"
            cases[
                "FORBIDDEN_OUTPUT_FIELD"
            ] = forbidden

            identity = copy.deepcopy(baseline)
            identity["profile_id"] = "paper2"
            cases["PROFILE_ID_MISMATCH"] = identity

            signature = copy.deepcopy(baseline)
            signature["input_signature"] = "0" * 64
            cases[
                "INPUT_SIGNATURE_MISMATCH"
            ] = signature

            expired = copy.deepcopy(baseline)
            expired["valid_until"] = expired[
                "generated_at"
            ]
            cases[
                "INVALID_VALIDITY_WINDOW"
            ] = expired

            future = copy.deepcopy(baseline)
            future["generated_at"] = (
                "2099-01-01T00:00:00+00:00"
            )
            future["valid_until"] = (
                "2099-01-02T00:00:00+00:00"
            )
            cases[
                "PORTFOLIO_GENERATED_IN_FUTURE"
            ] = future

            outside = copy.deepcopy(baseline)
            outside["decisions"][0][
                "symbol"
            ] = "NOTINCOARSE"
            cases[
                "NEW_POSITION_NOT_ELIGIBLE"
            ] = outside

            for expected_code, payload in cases.items():
                with self.subTest(code=expected_code):
                    result_value = (
                        validate_portfolio_output(
                            payload,
                            input_payload=input_payload,
                            schema=schema,
                        )
                    )
                    codes = {
                        item["code"]
                        for item
                        in result_value.errors
                    }
                    self.assertIn(
                        expected_code,
                        codes,
                    )


if __name__ == "__main__":
    unittest.main()
