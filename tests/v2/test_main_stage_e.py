"""验证 Stage E 主流程在 allow-trade 下仍以零订单停在 BUILD_ORDERS。

作用：运行完整 fake broker/Codex 链并检查用户可见摘要。
重要性：交易许可不能被误解为 Stage E 的订单提交授权。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from v2.main import (
    print_stage_e_result,
    run_stage_e,
)
from v2.models.state import StepName
from tests.v2.support import (
    FakeCoarseRunner,
    FakeExecutionRunner,
    FakePortfolioRunner,
    stage_d_clients,
    stage_d_options,
    stage_e_fixture,
)


class MainStageETests(unittest.TestCase):
    def test_allow_trade_still_submits_zero(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = stage_e_fixture(Path(temp))
            output = io.StringIO()
            with redirect_stdout(output):
                print_stage_e_result(result)
            text = output.getvalue()
            self.assertEqual(
                result.stopped_at,
                StepName.BUILD_ORDERS,
            )
            self.assertIn(
                "Profile：paper1",
                text,
            )
            self.assertIn(
                "策略：core_long@1.2.0",
                text,
            )
            self.assertIn(
                "下一步骤：BUILD_ORDERS",
                text,
            )
            self.assertIn(
                "提交订单数：0",
                text,
            )
            self.assertFalse(
                result.resolution.paths
                .broker_submission.exists()
            )

    def test_execution_refresh_reuses_same_day_portfolio(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = stage_e_fixture(root)
            second = run_stage_e(
                stage_d_options("--new-cycle"),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=(
                    FakePortfolioRunner()
                ),
                execution_runner=(
                    FakeExecutionRunner()
                ),
            )
            self.assertNotEqual(
                first.resolution.paths.cycle_id,
                second.resolution.paths.cycle_id,
            )
            self.assertEqual(
                second.stopped_at,
                StepName.BUILD_ORDERS,
            )
            self.assertTrue(
                second.resolution.paths
                .portfolio_reuse.is_file()
            )
            self.assertIsNotNone(
                second.resolution.state
                .reused_portfolio_cycle_id
            )


if __name__ == "__main__":
    unittest.main()
