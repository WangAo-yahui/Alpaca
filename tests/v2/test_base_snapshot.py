from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from v2.cli import parse_cli_args
from v2.data.alpaca_client import AlpacaClients
from v2.data.snapshots import create_base_snapshot
from v2.main import run_stage_b
from v2.models.state import StepName
from v2.runtime import build_cycle_paths
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_order,
    fake_position,
)
from tests.v2.support import (
    PROJECT_ROOT,
    copy_v2_config,
)


NY = ZoneInfo("America/New_York")


def clients(
    *,
    failures: set[str] | None = None,
) -> AlpacaClients:
    return AlpacaClients(
        trading=FakeTradingClient(
            positions=[fake_position("MU")],
            open_orders=[fake_order("MU")],
            today_orders=[fake_order("MU")],
            failures=failures,
        ),
        stock_data=FakeStockDataClient(),
    )


class BaseSnapshotTests(unittest.TestCase):
    def test_snapshot_is_atomic_and_schema_valid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T120502",
                project_root=root,
            )
            result = create_base_snapshot(
                paths,
                clients(),
                now=datetime(
                    2026,
                    7,
                    23,
                    10,
                    tzinfo=NY,
                ),
            )
            self.assertTrue(result.decision_ready)
            self.assertTrue(
                paths.base_snapshot.exists()
            )
            self.assertEqual(
                result.payload["capital"][
                    "open_order_reserved_estimate"
                ],
                420.0,
            )
            self.assertEqual(
                result.payload["capital"][
                    "allocatable_capital_estimate"
                ],
                9580.5,
            )
            schema = json.loads(
                (
                    PROJECT_ROOT
                    / "schemas/v2/base_snapshot.schema.json"
                ).read_text(encoding="utf-8")
            )
            validator = Draft202012Validator(
                schema,
                format_checker=(
                    Draft202012Validator.FORMAT_CHECKER
                ),
            )
            self.assertEqual(
                list(
                    validator.iter_errors(
                        result.payload
                    )
                ),
                [],
            )
            self.assertEqual(
                list(
                    paths.cycle_directory.glob(
                        ".base_snapshot.json.*.tmp"
                    )
                ),
                [],
            )

    def test_empty_positions_and_orders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T120502",
                project_root=Path(temporary),
            )
            result = create_base_snapshot(
                paths,
                AlpacaClients(
                    trading=FakeTradingClient(),
                    stock_data=(
                        FakeStockDataClient()
                    ),
                ),
            )
            self.assertEqual(
                result.payload["positions"],
                [],
            )
            self.assertEqual(
                result.payload["open_orders"],
                [],
            )
            self.assertTrue(result.decision_ready)

    def test_partial_asset_failure_is_warning(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T120502",
                project_root=Path(temporary),
            )
            result = create_base_snapshot(
                paths,
                clients(failures={"asset:MU"}),
            )
            self.assertTrue(result.decision_ready)
            self.assertFalse(
                result.payload["data_quality"][
                    "assets_complete"
                ]
            )
            self.assertTrue(
                result.payload["data_quality"][
                    "warnings"
                ]
            )

    def test_account_failure_blocks_decision_but_saves(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T120502",
                project_root=Path(temporary),
            )
            result = create_base_snapshot(
                paths,
                clients(failures={"account"}),
            )
            self.assertFalse(result.decision_ready)
            self.assertIsNone(
                result.payload["account"]
            )
            self.assertTrue(
                paths.base_snapshot.exists()
            )

    def test_stage_b_stops_before_codex_and_submission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            result = run_stage_b(
                parse_cli_args(
                    [
                        "--profile",
                        "paper1",
                        "--run-date",
                        "2026-07-23",
                        "--no-review",
                        "--no-guidance",
                        "--allow-trade",
                    ]
                ),
                project_root=root,
                clients=clients(),
            )
            self.assertTrue(result.data_refreshed)
            self.assertEqual(
                result.stopped_at,
                StepName.RUN_COARSE,
            )
            self.assertTrue(
                result.resolution.state
                .trade_permission
                .submission_enabled
            )
            self.assertFalse(
                result.resolution.state
                .trade_permission.dry_run
            )
            self.assertTrue(
                result.resolution.paths
                .base_snapshot.exists()
            )
            self.assertFalse(
                result.resolution.paths
                .broker_submission.exists()
            )


if __name__ == "__main__":
    unittest.main()
