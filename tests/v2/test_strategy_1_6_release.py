"""验证 core_long 1.6.0 的估值证据、现金和复核合同。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

from v2.codex.validation import preflight_output_schema
from v2.models.portfolio import validate_portfolio_output
from v2.profiles import load_profile, load_risk_profile
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.test_strategy_1_5_release import (
    StrategyOneFiveReleaseTests,
)


class StrategyOneSixReleaseTests(unittest.TestCase):
    def test_release_profile_and_minimum_hard_boundaries(self) -> None:
        release = load_strategy_release("core_long", "1.6.0")
        profile = load_profile("live1")
        risk = load_risk_profile(profile.risk_profile)
        self.assertEqual(profile.strategy_version, "1.6.0")
        profile_document = load_json_object(profile.source_path)
        self.assertEqual(profile_document["schedule"]["interval_minutes"], 120)
        self.assertEqual(risk.reference, "live_full@1.2.0")
        self.assertEqual(risk.settings["minimum_cash_weight"], "0.00")
        self.assertEqual(
            risk.settings["maximum_single_position_weight"], "0.40"
        )
        self.assertEqual(risk.settings["maximum_sector_weight"], "0.65")
        self.assertFalse(risk.settings["allow_short_positions"])

        schema = load_json_object(
            release.root / "schemas" / "portfolio_output.schema.json"
        )
        Draft202012Validator.check_schema(schema)
        preflight_output_schema(schema)
        self.assertIn("cash_management", schema["required"])
        decision_required = schema["properties"]["decisions"]["items"][
            "required"
        ]
        self.assertIn("monitoring_plan", decision_required)
        valuation_required = schema["properties"]["decisions"]["items"][
            "properties"
        ]["valuation"]["required"]
        self.assertIn("calculation_inputs", valuation_required)
        self.assertIn("attempted_methods", valuation_required)

        execution_schema = load_json_object(
            release.root / "schemas" / "execution_output.schema.json"
        )
        Draft202012Validator.check_schema(execution_schema)
        preflight_output_schema(execution_schema)
        execution_properties = execution_schema["properties"]
        self.assertEqual(
            execution_properties["strategy_id"]["const"],
            release.strategy_id,
        )
        self.assertEqual(
            execution_properties["strategy_version"]["const"],
            release.strategy_version,
        )

    def test_no_estimate_and_high_cash_are_time_bounded(self) -> None:
        now = datetime.now(timezone.utc)
        fixtures = StrategyOneFiveReleaseTests
        input_payload = fixtures._portfolio_input(
            now=now,
            candidates=[fixtures._candidate("A")],
        )
        input_payload["policy"].update(
            {
                "valuation_research": {
                    "minimum_attempted_methods_before_no_estimate": 2,
                    "minimum_calculation_inputs_for_estimate": 2,
                    "minimum_evidence_quality_for_open_or_increase": "medium",
                    "minimum_return_confidence_for_open_or_increase": "medium",
                },
                "cash_management": {
                    "high_cash_weight_threshold": "0.25",
                    "maximum_review_days": 14,
                    "minimum_deployment_triggers": 2,
                },
                "thesis_monitoring": {
                    "maximum_review_days": 30,
                    "minimum_triggers_per_decision": 2,
                },
            }
        )
        decision = fixtures._decision("A", "0")
        decision.update(
            {
                "action": "watch",
                "valuation": {
                    "status": "no_reliable_estimate",
                    "market_price": "10",
                    "value_range_low": None,
                    "value_range_high": None,
                    "margin_of_safety_fraction": None,
                    "evidence_quality": "insufficient",
                    "attempted_methods": [],
                    "calculation_inputs": [],
                    "source_references": [],
                    "no_estimate_reason": None,
                },
                "expected_return": {
                    "bear_annualized": None,
                    "base_annualized": None,
                    "bull_annualized": None,
                    "confidence": "insufficient",
                },
                "monitoring_plan": {
                    "review_by": "2026-10-01",
                    "triggers": [],
                },
            }
        )
        payload = fixtures._portfolio_output(
            now=now,
            cash="1",
            invested="0",
            decisions=[decision],
        )
        payload["cash_management"] = {
            "review_by": "2026-10-01",
            "deployment_triggers": [],
        }
        result = validate_portfolio_output(
            payload,
            input_payload=input_payload,
            schema={"type": "object"},
            now=now,
        )
        codes = {item["code"] for item in result.errors}
        self.assertIn("UNRELIABLE_VALUATION_METHODS_INSUFFICIENT", codes)
        self.assertIn("UNRELIABLE_VALUATION_REASON_MISSING", codes)
        self.assertIn("HIGH_CASH_DEPLOYMENT_TRIGGERS_INSUFFICIENT", codes)
        self.assertIn("HIGH_CASH_REVIEW_DATE_INVALID", codes)
        self.assertIn("MONITORING_TRIGGERS_INSUFFICIENT", codes)
        self.assertIn("MONITORING_REVIEW_DATE_INVALID", codes)


if __name__ == "__main__":
    unittest.main()
