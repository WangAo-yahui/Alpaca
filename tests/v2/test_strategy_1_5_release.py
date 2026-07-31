"""验证 core_long 1.5.0 的三级漏斗与反锚定行为合同。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path

from jsonschema import Draft202012Validator

from v2.codex.validation import (
    preflight_output_schema,
    validate_coarse_output,
)
from v2.config import load_config
from v2.models.coarse import build_coarse_input
from v2.models.portfolio import (
    validate_portfolio_output,
)
from v2.profiles import load_profile
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    prepare_stage_c_project,
    valid_coarse_output,
)


class StrategyOneFiveReleaseTests(
    unittest.TestCase
):
    def test_release_and_funnel_contract(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.5.0",
        )
        self.assertEqual(
            load_profile("live1").strategy_version,
            "1.5.0",
        )
        self.assertEqual(
            load_profile("paper1").strategy_version,
            "1.2.0",
        )
        coarse_schema = load_json_object(
            release.root
            / "schemas"
            / "coarse_output.schema.json"
        )
        portfolio_schema = load_json_object(
            release.root
            / "schemas"
            / "portfolio_output.schema.json"
        )
        Draft202012Validator.check_schema(
            coarse_schema
        )
        Draft202012Validator.check_schema(
            portfolio_schema
        )
        preflight_output_schema(coarse_schema)
        self.assertEqual(
            coarse_schema["properties"][
                "selection_count"
            ]["const"],
            20,
        )
        self.assertIn(
            "etf_lookthrough",
            portfolio_schema["required"],
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(
                root,
                stock_count=140,
            )
            policy = load_json_object(
                root
                / "strategies/core_long/1.5.0"
                / "config/coarse_policy.json"
            )
            result = build_coarse_input(
                config=load_config(
                    project_root=root
                ),
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": (
                        "regular_session"
                    ),
                    "positions": [],
                    "open_orders": [],
                    "assets": [],
                },
                strategy_version="1.5.0",
                coarse_policy=policy,
            )
            output = valid_coarse_output(
                result.payload,
                status="success",
            )
            validation = validate_coarse_output(
                output,
                input_payload=result.payload,
                schema=coarse_schema,
            )

        self.assertLessEqual(
            len(result.payload["universe"]),
            120,
        )
        self.assertEqual(
            result.payload["policy"][
                "required_selection_count"
            ],
            20,
        )
        self.assertEqual(
            output["selection_count"],
            20,
        )
        self.assertTrue(
            validation.valid,
            validation.errors,
        )
        self.assertTrue(
            all(
                item["research_features"][
                    "model_version"
                ].endswith("_v2")
                for item in result.payload[
                    "universe"
                ]
            )
        )

    def test_holding_and_must_include_do_not_add_score(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(
                root,
                stock_count=140,
            )
            universe_path = (
                root
                / "config/v2/universe.json"
            )
            universe = json.loads(
                universe_path.read_text(
                    encoding="utf-8"
                )
            )
            universe["must_include"] = ["S139"]
            universe_path.write_text(
                json.dumps(universe),
                encoding="utf-8",
            )
            policy = load_json_object(
                root
                / "strategies/core_long/1.5.0"
                / "config/coarse_policy.json"
            )
            config = load_config(project_root=root)
            plain = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": (
                        "regular_session"
                    ),
                    "positions": [],
                    "open_orders": [],
                    "assets": [],
                },
                strategy_version="1.5.0",
                coarse_policy=policy,
            ).payload
            held = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": (
                        "regular_session"
                    ),
                    "positions": [
                        {"symbol": "S010"}
                    ],
                    "open_orders": [],
                    "assets": [],
                },
                strategy_version="1.5.0",
                coarse_policy=policy,
            ).payload

        def shortlist_by_symbol(
            payload: dict,
        ) -> dict[str, dict]:
            return {
                item["symbol"]: item
                for item in payload[
                    "python_shortlists"
                ]["stock"]
            }

        plain_ranked = shortlist_by_symbol(plain)
        held_ranked = shortlist_by_symbol(held)
        self.assertEqual(
            plain_ranked["S010"][
                "research_priority_score"
            ],
            held_ranked["S010"][
                "research_priority_score"
            ],
        )
        self.assertEqual(
            plain_ranked["S010"]["rank"],
            held_ranked["S010"]["rank"],
        )
        self.assertEqual(
            plain_ranked["S139"][
                "research_priority_score"
            ],
            plain_ranked["S000"][
                "research_priority_score"
            ],
        )
        self.assertNotIn(
            "must_include",
            plain_ranked["S139"][
                "signal_tags"
            ],
        )

    def test_portfolio_prompt_disambiguates_required_comparators(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.5.0",
        )
        prompt = (
            release.root
            / "prompts"
            / "portfolio.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "first three symbols by coarse `rank`",
            prompt,
        )
        self.assertIn(
            '`comparator_type="cash"` and `current_position=false`',
            prompt,
        )
        self.assertIn(
            "`valuation.evidence_quality` exactly to `insufficient`",
            prompt,
        )
        self.assertIn(
            "Missing fields in the supplied Python screen are a research task",
            prompt,
        )
        self.assertIn(
            "after an actual web-search attempt fails technically",
            prompt,
        )

        execution_prompt = (
            release.root
            / "prompts"
            / "execution.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "`execution_snapshot.market_phase`",
            execution_prompt,
        )
        self.assertIn(
            "不得追加 `_analysis_only`",
            execution_prompt,
        )
        self.assertIn(
            "`portfolio_action` 必须逐字复制",
            execution_prompt,
        )
        self.assertIn(
            '`execution_decision="no_action"`',
            execution_prompt,
        )
        self.assertIn(
            "不得把原动作改写成 `hold`",
            execution_prompt,
        )

        execution_agents = (
            release.root
            / "prompts"
            / "execution_AGENTS.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "`portfolio_action` 必须逐字复制",
            execution_agents,
        )
        self.assertIn(
            "`execution_decision=no_action`",
            execution_agents,
        )

    def test_unknown_sectors_are_aggregated(
        self,
    ) -> None:
        now = datetime.now(timezone.utc)
        input_payload = self._portfolio_input(
            now=now,
            candidates=[
                self._candidate("A"),
                self._candidate("B"),
            ],
            maximum_sector_weight="0.50",
        )
        payload = self._portfolio_output(
            now=now,
            cash="0.40",
            invested="0.60",
            decisions=[
                self._decision("A", "0.30"),
                self._decision("B", "0.30"),
            ],
        )
        result = validate_portfolio_output(
            payload,
            input_payload=input_payload,
            schema={"type": "object"},
        )
        self.assertIn(
            "SECTOR_LIMIT_BREACHED",
            {
                item["code"]
                for item in result.errors
            },
        )

    def test_capital_competition_and_etf_contracts(
        self,
    ) -> None:
        now = datetime.now(timezone.utc)
        candidates = [
            self._candidate("HOLD"),
            self._candidate("A"),
            self._candidate("B"),
            self._candidate("C"),
            self._candidate(
                "ETF1",
                asset_type="etf",
                sector="ETF",
            ),
        ]
        input_payload = self._portfolio_input(
            now=now,
            candidates=candidates,
            positions=[
                {
                    "symbol": "HOLD",
                    "current_weight": "0.20",
                    "market_value": "20",
                },
                {
                    "symbol": "ETF1",
                    "current_weight": "0.20",
                    "market_value": "20",
                },
            ],
            competition=True,
        )
        payload = self._portfolio_output(
            now=now,
            cash="1",
            invested="0",
            decisions=[],
        )
        payload["capital_competition"] = {
            "ranked_uses": [
                {
                    "rank": 1,
                    "symbol": "CASH",
                    "comparator_type": "cash",
                }
            ],
            "holding_counterfactuals": [],
        }
        payload["etf_lookthrough"] = {
            "status": "partial",
            "assessments": [],
        }
        result = validate_portfolio_output(
            payload,
            input_payload=input_payload,
            schema={"type": "object"},
        )
        codes = {
            item["code"] for item in result.errors
        }
        self.assertIn(
            "NON_HELD_COMPARATORS_INSUFFICIENT",
            codes,
        )
        self.assertIn(
            "HOLDING_COUNTERFACTUAL_MISSING",
            codes,
        )
        self.assertIn(
            "ETF_LOOKTHROUGH_MISSING",
            codes,
        )

    @staticmethod
    def _candidate(
        symbol: str,
        *,
        asset_type: str = "stock",
        sector: str | None = None,
    ) -> dict:
        return {
            "symbol": symbol,
            "asset_type": asset_type,
            "sector": sector,
            "screen_new_position_eligible": True,
        }

    @staticmethod
    def _decision(
        symbol: str,
        weight: str,
    ) -> dict:
        return {
            "symbol": symbol,
            "current_position": False,
            "in_current_coarse": True,
            "action": "open",
            "target_weight": weight,
            "maximum_weight": weight,
            "accumulation_plan": {
                "style": "immediate",
                "planned_total_fraction": "1",
                "tranches": [
                    {
                        "fraction": "1",
                        "price_trigger_low": None,
                        "price_trigger_high": None,
                    }
                ],
            },
        }

    @staticmethod
    def _portfolio_input(
        *,
        now: datetime,
        candidates: list[dict],
        positions: list[dict] | None = None,
        maximum_sector_weight: str = "1",
        competition: bool = False,
    ) -> dict:
        requirements = (
            {
                "enabled": True,
                "require_cash_comparator": True,
                "minimum_non_held_comparators": 3,
                "require_top_ranked_non_held": True,
                "require_all_holdings_as_comparators": True,
                "require_counterfactual_for_each_holding": True,
                "increase_requires_would_buy_if_not_held": True,
                "require_etf_lookthrough_for_held_or_positive": True,
            }
            if competition
            else {}
        )
        return {
            "profile": {"profile_id": "live1"},
            "release": {
                "strategy_id": "core_long",
                "strategy_version": "1.5.0",
            },
            "run_date": "2026-07-30",
            "cycle_id": "20260730T120000",
            "input_signature": "c" * 64,
            "policy": {
                "maximum_sector_weight": (
                    maximum_sector_weight
                ),
                "minimum_target_weight": "0.01",
                "allow_empty_portfolio": True,
                "target_holdings": {
                    "minimum": 0,
                    "maximum": 20,
                },
                "risk_profile": {
                    "maximum_single_position_weight": "1",
                    "minimum_cash_weight": "0",
                    "maximum_gross_exposure": "1",
                },
                "capital_competition_requirements": (
                    requirements
                ),
            },
            "positions": positions or [],
            "candidates": candidates,
            "open_orders": [],
            "account": {
                "portfolio_value": "100"
            },
            "capital": {
                "allocatable_capital_estimate": (
                    "100"
                )
            },
            "generated_at": now.isoformat(),
        }

    @staticmethod
    def _portfolio_output(
        *,
        now: datetime,
        cash: str,
        invested: str,
        decisions: list[dict],
    ) -> dict:
        return {
            "stage": "portfolio_decision",
            "profile_id": "live1",
            "strategy_id": "core_long",
            "strategy_version": "1.5.0",
            "run_date": "2026-07-30",
            "cycle_id": "20260730T120000",
            "input_signature": "c" * 64,
            "status": "success",
            "generated_at": now.isoformat(),
            "valid_until": (
                now + timedelta(hours=1)
            ).isoformat(),
            "allocation": {
                "target_cash_weight": cash,
                "target_invested_weight": invested,
                "target_position_count": sum(
                    decision["target_weight"]
                    != "0"
                    for decision in decisions
                ),
                "maximum_single_symbol_weight": "1",
                "maximum_sector_weight": "1",
            },
            "decisions": decisions,
            "guidance_response": {},
        }


if __name__ == "__main__":
    unittest.main()
