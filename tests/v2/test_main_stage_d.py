"""验证主链运行 Stage D 后精确停在执行数据刷新之前。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.main import run_stage_d
from v2.models.state import StepName
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class MainStageDTests(unittest.TestCase):
    def test_unattended_allow_trade_still_submits_zero(
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
            self.assertEqual(
                result.stopped_at,
                StepName.REFRESH_EXECUTION_DATA,
            )
            self.assertTrue(
                result.resolution.state
                .trade_permission.submission_enabled
            )
            self.assertEqual(
                result.review.mode,
                "skipped_by_flag",
            )
            paths = result.resolution.paths
            for forbidden in (
                paths.execution_output,
                paths.proposed_orders,
                paths.validated_orders,
                paths.broker_submission,
            ):
                self.assertFalse(
                    forbidden.exists()
                )


if __name__ == "__main__":
    unittest.main()
