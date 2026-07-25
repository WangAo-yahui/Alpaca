"""验证同日组合复用以及持仓、挂单、资本和 force 的重跑条件。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.fakes import (
    fake_order,
    fake_position,
)
from v2.main import run_stage_d
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class PortfolioReuseTests(unittest.TestCase):
    def _first(
        self,
        root: Path,
        runner: FakePortfolioRunner,
    ):
        return run_stage_d(
            stage_d_options(),
            project_root=root,
            clients=stage_d_clients(),
            coarse_runner=FakeCoarseRunner(),
            portfolio_runner=runner,
        )

    def test_same_facts_reuse_and_small_capital_change_reuses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            runner = FakePortfolioRunner()
            self._first(root, runner)
            second = run_stage_d(
                stage_d_options("--new-cycle"),
                project_root=root,
                clients=stage_d_clients(
                    cash="10050.00"
                ),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=runner,
            )
            self.assertEqual(runner.calls, 1)
            assert second.portfolio is not None
            self.assertTrue(second.portfolio.reused)
            self.assertTrue(
                second.resolution.paths
                .portfolio_reuse.is_file()
            )

    def test_position_order_capital_and_force_changes_run(
        self,
    ) -> None:
        variants = [
            (
                stage_d_clients(
                    positions=[
                        fake_position(
                            "S064",
                            qty="11",
                        )
                    ]
                ),
                (),
            ),
            (
                stage_d_clients(
                    orders=[
                        fake_order(
                            "S063",
                            limit_price="110",
                        )
                    ]
                ),
                (),
            ),
            (
                stage_d_clients(cash="10250.50"),
                (),
            ),
            (
                stage_d_clients(),
                ("--force-rebalance",),
            ),
        ]
        for index, (clients, flags) in enumerate(
            variants
        ):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    prepare_stage_c_project(root)
                    runner = FakePortfolioRunner()
                    self._first(root, runner)
                    second = run_stage_d(
                        stage_d_options(
                            "--new-cycle",
                            *flags,
                        ),
                        project_root=root,
                        clients=clients,
                        coarse_runner=FakeCoarseRunner(),
                        portfolio_runner=runner,
                    )
                    self.assertEqual(
                        runner.calls,
                        2,
                    )
                    assert second.portfolio is not None
                    self.assertFalse(
                        second.portfolio.reused
                    )


if __name__ == "__main__":
    unittest.main()
