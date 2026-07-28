"""验证 Stage D 输入完整传递 guidance、资金、候选、持仓与挂单。"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from v2.main import run_stage_d
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class PortfolioInputTests(unittest.TestCase):
    def test_input_contains_required_decision_facts(
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
            assert result.portfolio is not None
            payload = result.portfolio.input_result.payload
            self.assertEqual(
                len(payload["candidates"]),
                60,
            )
            quote_references = [
                item["latest_quote"]
                for item in payload["candidates"]
            ]
            self.assertTrue(
                any(
                    item["status"]
                    in {
                        "position_snapshot",
                        "daily_close",
                    }
                    for item in quote_references
                )
            )
            self.assertTrue(
                all(
                    item["is_live_quote"] is False
                    and item[
                        "execution_revalidation_required"
                    ]
                    is True
                    for item in quote_references
                )
            )
            self.assertEqual(
                Decimal(
                    payload["open_orders"][0][
                        "reserved_capital_estimate"
                    ]
                ),
                Decimal("420"),
            )
            self.assertIn(
                "guidance_hash",
                payload["initial_guidance"],
            )
            self.assertIn(
                "risk_profile_hash",
                payload["release"],
            )


if __name__ == "__main__":
    unittest.main()
