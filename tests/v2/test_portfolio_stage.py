"""验证 Stage D run 成功、失败保留旧索引且不产生订单。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from v2.exceptions import (
    CodexOutputValidationError,
)
from v2.main import run_stage_d, run_stage_e
from v2.models.state import (
    StepName,
    load_daily_state,
)
from tests.v2.support import (
    FakeCoarseRunner,
    FakeExecutionRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)
from v2.runtime import load_json_object


class PortfolioStageTests(unittest.TestCase):
    def test_future_model_timing_is_normalized_before_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)

            def future_timing(output: dict) -> None:
                output["generated_at"] = (
                    "2099-01-01T00:00:00+00:00"
                )
                output["valid_until"] = (
                    "2099-01-02T00:00:00+00:00"
                )

            result = run_stage_e(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=FakePortfolioRunner(
                    mutate=future_timing
                ),
                execution_runner=FakeExecutionRunner(),
            )
            self.assertIsNotNone(result.execution)
            paths = result.resolution.paths
            portfolio = load_json_object(
                paths.portfolio_output
            )
            snapshot = load_json_object(
                paths.execution_snapshot
            )
            self.assertLess(
                datetime.fromisoformat(
                    portfolio["generated_at"]
                ),
                datetime.fromisoformat(
                    snapshot["retrieved_at"]
                ),
            )
            call = load_json_object(
                paths.portfolio_codex_call
            )
            self.assertEqual(
                call["safe_normalizations"][0]["type"],
                "application_owned_portfolio_timing",
            )

    def test_run_stops_before_execution_and_orders(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            runner = FakePortfolioRunner()
            result = run_stage_d(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=runner,
            )
            self.assertEqual(runner.calls, 1)
            self.assertEqual(
                result.stopped_at,
                StepName.REFRESH_EXECUTION_DATA,
            )
            paths = result.resolution.paths
            self.assertTrue(
                paths.portfolio_output.is_file()
            )
            self.assertFalse(
                paths.execution_output.exists()
            )
            self.assertFalse(
                paths.proposed_orders.exists()
            )
            self.assertFalse(
                paths.broker_submission.exists()
            )

    def test_invalid_new_output_keeps_previous_index(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            first = run_stage_d(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=FakePortfolioRunner(),
            )
            previous = load_daily_state(
                first.resolution.paths.daily_state
            ).latest_valid_portfolio_output_path

            def break_output(output: dict) -> None:
                output["allocation"][
                    "target_cash_weight"
                ] = "0"

            with self.assertRaises(
                CodexOutputValidationError
            ):
                run_stage_d(
                    stage_d_options(
                        "--new-cycle",
                        "--force-rebalance",
                    ),
                    project_root=root,
                    clients=stage_d_clients(),
                    coarse_runner=FakeCoarseRunner(),
                    portfolio_runner=FakePortfolioRunner(
                        mutate=break_output
                    ),
                )
            daily = load_daily_state(
                first.resolution.paths.daily_state
            )
            self.assertEqual(
                previous,
                daily.latest_valid_portfolio_output_path,
            )


if __name__ == "__main__":
    unittest.main()
