"""验证完整 Stage F 主流程边界。

作用：运行 fake broker/Codex 链并检查六个订单产物与状态机终点。
重要性：有无 --allow-trade 都必须停在 SUBMIT_ORDERS 且实际提交为零。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from v2.main import (
    print_stage_f_result,
    run_stage_f,
)
from v2.models.state import (
    CycleStatus,
    StepName,
)
from tests.v2.support import (
    FakeCoarseRunner,
    FakeExecutionRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class MainStageFTests(unittest.TestCase):
    def _run(self, *, allow_trade: bool):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        prepare_stage_c_project(root)
        clients = stage_d_clients()
        result = run_stage_f(
            replace(
                stage_d_options(),
                allow_trade=allow_trade,
            ),
            project_root=root,
            clients=clients,
            coarse_runner=FakeCoarseRunner(),
            portfolio_runner=FakePortfolioRunner(),
            execution_runner=FakeExecutionRunner(),
        )
        return result, clients

    def test_dry_run_stops_at_submit_with_artifacts(
        self,
    ) -> None:
        result, _ = self._run(
            allow_trade=False
        )
        paths = result.resolution.paths
        self.assertEqual(
            result.stopped_at,
            StepName.SUBMIT_ORDERS,
        )
        self.assertEqual(
            result.resolution.state.current_step,
            StepName.SUBMIT_ORDERS,
        )
        self.assertEqual(
            result.resolution.state.status,
            CycleStatus.RUNNING,
        )
        for path in (
            paths.pretrade_snapshot,
            paths.proposed_orders,
            paths.validated_orders,
            paths.order_request_specs,
            paths.order_action_plan,
            paths.order_validation_summary,
        ):
            self.assertTrue(path.is_file(), path)
        self.assertFalse(
            paths.broker_submission.exists()
        )

    def test_allow_trade_only_approves_plan_and_submits_zero(
        self,
    ) -> None:
        result, clients = self._run(
            allow_trade=True
        )
        output = io.StringIO()
        with redirect_stdout(output):
            print_stage_f_result(result)
        text = output.getvalue()
        self.assertIn(
            "下一步骤：SUBMIT_ORDERS",
            text,
        )
        self.assertIn(
            "实际提交订单数：0",
            text,
        )
        self.assertFalse(
            hasattr(
                clients.trading,
                "submitted_orders",
            )
        )
        self.assertFalse(
            result.resolution.paths
            .broker_submission.exists()
        )

    def test_permission_change_creates_fresh_execution_cycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            clients = stage_d_clients()
            dry = run_stage_f(
                replace(
                    stage_d_options(),
                    allow_trade=False,
                ),
                project_root=root,
                clients=clients,
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=(
                    FakePortfolioRunner()
                ),
                execution_runner=(
                    FakeExecutionRunner()
                ),
            )
            allowed = run_stage_f(
                replace(
                    stage_d_options(),
                    allow_trade=True,
                ),
                project_root=root,
                clients=clients,
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=(
                    FakePortfolioRunner()
                ),
                execution_runner=(
                    FakeExecutionRunner()
                ),
            )
        self.assertNotEqual(
            dry.resolution.paths.cycle_id,
            allowed.resolution.paths.cycle_id,
        )
        self.assertTrue(
            allowed.validated_document[
                "submission_requested"
            ]
        )
        self.assertEqual(
            allowed.stopped_at,
            StepName.SUBMIT_ORDERS,
        )


if __name__ == "__main__":
    unittest.main()
