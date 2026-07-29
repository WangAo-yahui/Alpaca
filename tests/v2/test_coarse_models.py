"""验证 Coarse 客观输入、硬筛选和共享资产缓存兼容性。

作用：检查技术摘要、输入签名、持仓保留以及资产能力合并。
重要性：资产缓存解析错误会把整个候选池误判为不可开仓并生成空决策。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v2.config import load_config
from v2.models.coarse import (
    CoarseInput,
    build_coarse_input,
    summarize_daily_bars,
)
from v2.runtime import load_json_object
from tests.v2.support import (
    prepare_stage_c_project,
)


class CoarseModelTests(unittest.TestCase):
    def test_python_shortlists_rank_stocks_and_etfs_separately(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(
                root,
                stock_count=110,
            )
            policy = load_json_object(
                root
                / "strategies"
                / "core_long"
                / "1.4.0"
                / "config"
                / "coarse_policy.json"
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
                strategy_version="1.4.0",
                coarse_policy=policy,
            )
        shortlists = result.payload[
            "python_shortlists"
        ]
        self.assertEqual(
            len(shortlists["stock"]),
            100,
        )
        self.assertEqual(
            len(shortlists["etf"]),
            6,
        )
        self.assertTrue(
            all(
                item["score_model"]
                == "stock_research_priority_v1"
                for item in shortlists["stock"]
            )
        )
        self.assertTrue(
            all(
                item["score_model"]
                == "etf_research_priority_v1"
                for item in shortlists["etf"]
            )
        )

    def test_daily_summary_has_required_metrics(
        self,
    ) -> None:
        bars = [
            {
                "timestamp": (
                    f"2026-01-{index + 1:02d}"
                ),
                "open": 99 + index,
                "high": 101 + index,
                "low": 98 + index,
                "close": 100 + index,
                "volume": 100000 + index,
            }
            for index in range(20)
        ]
        summary, warnings = (
            summarize_daily_bars(bars)
        )
        self.assertEqual(
            summary["bars_available"],
            20,
        )
        self.assertEqual(warnings, [])
        self.assertIsNotNone(
            summary["return_5d"]
        )
        self.assertIsNone(
            summary["return_20d"]
        )
        self.assertIsNotNone(
            summary[
                "average_dollar_volume_20d"
            ]
        )
        self.assertIn("rsi_14", summary)
        self.assertIn(
            "distance_from_sma_200",
            summary,
        )

    def test_signature_ignores_cash_and_positions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            config = load_config(
                project_root=root
            )
            first = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "regular_session",
                    "account": {"cash": 1000},
                    "positions": [],
                    "open_orders": [],
                    "assets": [],
                },
            )
            second = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "after_market_close",
                    "account": {"cash": 999999},
                    "positions": [
                        {
                            "symbol": "S000",
                            "quantity": 1,
                        }
                    ],
                    "open_orders": [
                        {"symbol": "S001"}
                    ],
                    "assets": [],
                },
            )
            self.assertEqual(
                first.input_signature,
                second.input_signature,
            )
            self.assertEqual(
                CoarseInput.from_dict(
                    second.payload
                ).to_dict(),
                second.payload,
            )
            second_items = {
                item["symbol"]: item
                for item in second.payload[
                    "universe"
                ]
            }
            self.assertTrue(
                second_items["S000"][
                    "currently_held"
                ]
            )
            self.assertTrue(
                second_items["S001"][
                    "has_open_order"
                ]
            )
            external = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "regular_session",
                    "positions": [
                        {"symbol": "LEGACY"}
                    ],
                    "open_orders": [],
                    "assets": [
                        {
                            "symbol": "LEGACY",
                            "name": "Legacy Holding",
                            "status": "active",
                            "tradable": False,
                        }
                    ],
                },
            )
            self.assertEqual(
                first.input_signature,
                external.input_signature,
            )
            external_item = next(
                item
                for item in external.payload[
                    "universe"
                ]
                if item["symbol"] == "LEGACY"
            )
            self.assertTrue(
                external_item[
                    "currently_held"
                ]
            )
            self.assertFalse(
                external_item[
                    "screen_new_position_eligible"
                ]
            )

    def test_held_symbol_overrides_hard_filter(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            assets_path = (
                root / "data/snapshots/assets.json"
            )
            assets = json.loads(
                assets_path.read_text(
                    encoding="utf-8"
                )
            )
            for asset in assets["assets"]:
                if asset["symbol"] in {
                    "S000",
                    "S001",
                }:
                    asset["tradable"] = False
            assets_path.write_text(
                json.dumps(assets),
                encoding="utf-8",
            )
            result = build_coarse_input(
                config=load_config(
                    project_root=root
                ),
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "regular_session",
                    "positions": [
                        {"symbol": "S000"}
                    ],
                    "open_orders": [],
                    "assets": [],
                },
            )
            items = {
                item["symbol"]: item
                for item in result.payload[
                    "universe"
                ]
            }
            self.assertIn("S000", items)
            self.assertFalse(
                items["S000"][
                    "screen_new_position_eligible"
                ]
            )
            self.assertNotIn("S001", items)
            self.assertTrue(
                any(
                    warning.startswith(
                        "PRESERVED_OVERRIDE:"
                    )
                    for warning in items["S000"][
                        "data_quality"
                    ]["warnings"]
                )
            )

    def test_nested_shared_asset_envelope_is_eligible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            assets_path = (
                root / "data/snapshots/assets.json"
            )
            original = json.loads(
                assets_path.read_text(
                    encoding="utf-8"
                )
            )
            assets_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "data": {
                            "assets": original["assets"]
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = build_coarse_input(
                config=load_config(
                    project_root=root
                ),
                run_date="2026-07-23",
                base_snapshot={
                    "market_phase": "overnight_session",
                    "positions": [],
                    "open_orders": [],
                    "assets": [],
                },
            )
        item = next(
            candidate
            for candidate in result.payload["universe"]
            if candidate["symbol"] == "S000"
        )
        self.assertTrue(
            item["screen_new_position_eligible"]
        )

    def test_asset_eligibility_changes_revision_signature(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            config = load_config(project_root=root)
            base_snapshot = {
                "market_phase": "regular_session",
                "positions": [],
                "open_orders": [],
                "assets": [],
            }
            first = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot=base_snapshot,
            )
            assets_path = (
                root / "data/snapshots/assets.json"
            )
            assets = json.loads(
                assets_path.read_text(
                    encoding="utf-8"
                )
            )
            for asset in assets["assets"]:
                if asset["symbol"] == "S000":
                    asset["tradable"] = False
            assets_path.write_text(
                json.dumps(assets),
                encoding="utf-8",
            )
            second = build_coarse_input(
                config=config,
                run_date="2026-07-23",
                base_snapshot=base_snapshot,
            )
        self.assertNotEqual(
            first.input_signature,
            second.input_signature,
        )
        self.assertIn(
            "S000",
            first.candidate_symbols,
        )
        self.assertNotIn(
            "S000",
            second.candidate_symbols,
        )


if __name__ == "__main__":
    unittest.main()
