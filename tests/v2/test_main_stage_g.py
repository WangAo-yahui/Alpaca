"""验证完整 Stage G dry-run 与自然 no-action 主流程。

作用：运行 fake broker/Codex 全链并检查九个订单产物、日报和 COMPLETE 状态。
重要性：无 allow 时写调用必须为零；allow 但自然无批准订单也应正常成功。
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from v2.exceptions import TemporaryDataError
from v2.main import (
    _raise_runtime_interrupted,
    run_stage_g,
)
from v2.models.state import CycleStatus, StepName
from v2.runtime import load_json_object
from tests.v2.support import (
    FakeCoarseRunner,
    FakeExecutionRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class MainStageGTests(unittest.TestCase):
    @staticmethod
    def _defer_all(output) -> None:
        """Keep the natural-no-order test independent of wall-clock phase."""

        for item in output["decisions"]:
            item["execution_decision"] = "defer"
            item["side"] = "none"
            item["execution_fraction"] = "0"
            item["urgency"] = "none"
            item["price_condition"] = {
                "reference": "none",
                "limit_price": None,
                "do_not_execute_above": None,
                "review_below": None,
            }
            item["order_intent"] = {
                "preferred_type": "none",
                "time_in_force_preference": "none",
                "extended_hours_requested": False,
                "allow_queue": False,
                "allow_partial_fill": False,
            }
        output["protection_plans"] = [
            item
            for item in output[
                "protection_plans"
            ]
            if item.get("apply_to")
            in {
                "existing_position",
                "both",
            }
        ]

    def _run_stage_g(
        self,
        root: Path,
        *,
        allow_trade: bool,
    ):
        return run_stage_g(
            replace(
                stage_d_options(),
                run_date="2026-07-25",
                allow_trade=allow_trade,
            ),
            project_root=root,
            clients=stage_d_clients(),
            coarse_runner=FakeCoarseRunner(),
            portfolio_runner=FakePortfolioRunner(),
            execution_runner=FakeExecutionRunner(
                mutate=(
                    self._defer_all
                    if allow_trade
                    else None
                )
            ),
        )

    def test_dry_run_completes_with_all_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_stage_c_project(root)
            result = self._run_stage_g(
                root, allow_trade=False
            )
            paths = result.resolution.paths
            self.assertEqual(
                result.resolution.state.status,
                CycleStatus.COMPLETED_DRY_RUN,
            )
            self.assertEqual(
                result.resolution.state.current_step,
                StepName.COMPLETE,
            )
            for path in (
                paths.submission_intent,
                paths.submission_journal,
                paths.broker_submission,
                paths.reconciliation,
                paths.cycle_summary,
                paths.daily_report,
            ):
                self.assertTrue(path.is_file(), path)
            self.assertFalse(
                result.broker_submission[
                    "submission_performed"
                ]
            )

    def test_automatically_created_client_is_used_for_performance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_stage_c_project(root)
            clients = stage_d_clients()
            clients.trading.get_portfolio_history = (
                lambda request: SimpleNamespace(
                    timestamp=[
                        int(
                            datetime(
                                2026,
                                7,
                                24,
                                tzinfo=timezone.utc,
                            ).timestamp()
                        )
                    ],
                    equity=["24900"],
                )
            )
            clients.trading.get = (
                lambda path, data: []
            )
            with patch(
                "v2.main.create_alpaca_clients",
                return_value=clients,
            ):
                result = run_stage_g(
                    replace(
                        stage_d_options(),
                        run_date="2026-07-25",
                        allow_trade=False,
                    ),
                    project_root=root,
                    clients=None,
                    coarse_runner=FakeCoarseRunner(),
                    portfolio_runner=FakePortfolioRunner(),
                    execution_runner=FakeExecutionRunner(),
                )
            performance = load_json_object(
                result.resolution.paths.day_directory
                / "performance.json"
            )
            self.assertEqual(
                performance["status"],
                "complete",
            )
            self.assertEqual(
                performance["history_start"],
                "2026-07-23",
            )

    def test_allow_with_natural_no_orders_is_no_action(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_stage_c_project(root)
            result = self._run_stage_g(
                root, allow_trade=True
            )
            self.assertEqual(
                result.resolution.state.status,
                CycleStatus.COMPLETED_NO_ACTION,
            )
            self.assertEqual(
                result.broker_submission[
                    "submitted_count"
                ],
                0,
            )

    def test_runtime_signal_is_retryable(
        self,
    ) -> None:
        with self.assertRaises(
            TemporaryDataError
        ) as context:
            _raise_runtime_interrupted(15, object())
        self.assertEqual(
            context.exception.code,
            "RUN_INTERRUPTED",
        )

    def test_maintenance_only_skips_decision_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_stage_c_project(root)
            result = run_stage_g(
                replace(
                    stage_d_options(),
                    run_date="2026-07-25",
                    allow_trade=False,
                    maintenance_only=True,
                ),
                project_root=root,
                clients=stage_d_clients(),
            )
            self.assertEqual(
                result.resolution.state.status,
                CycleStatus.COMPLETED_NO_ACTION,
            )
            self.assertIsNone(result.stage_f_result)
            self.assertFalse(
                result.resolution.paths
                .execution_output.exists()
            )


if __name__ == "__main__":
    unittest.main()
