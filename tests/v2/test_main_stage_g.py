"""验证完整 Stage G dry-run 与自然 no-action 主流程。

作用：运行 fake broker/Codex 全链并检查九个订单产物、日报和 COMPLETE 状态。
重要性：无 allow 时写调用必须为零；allow 但自然无批准订单也应正常成功。
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from v2.main import run_stage_g
from v2.models.state import CycleStatus, StepName
from tests.v2.support import (
    FakeCoarseRunner,
    FakeExecutionRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class MainStageGTests(unittest.TestCase):
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
            execution_runner=FakeExecutionRunner(),
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
