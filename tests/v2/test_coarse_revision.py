from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.data.alpaca_client import AlpacaClients
from v2.main import run_stage_c
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_account,
)
from tests.v2.support import (
    FakeCoarseRunner,
    prepare_stage_c_project,
)


def clients() -> AlpacaClients:
    return AlpacaClients(
        trading=FakeTradingClient(
            account=fake_account(),
            positions=[],
            open_orders=[],
            today_orders=[],
        ),
        stock_data=FakeStockDataClient(),
    )


def options(
    guidance: str,
    *,
    new_cycle: bool = False,
):
    arguments = [
        "--run-date",
        "2026-07-23",
        "--profile",
        "paper1",
        "--guidance",
        guidance,
        "--no-review",
    ]
    if new_cycle:
        arguments.append("--new-cycle")
    return parse_cli_args(arguments)


class CoarseRevisionTests(unittest.TestCase):
    def test_same_guidance_reuses_and_change_revises(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            runner = FakeCoarseRunner()
            first = run_stage_c(
                options("考虑MU"),
                project_root=root,
                clients=clients(),
                coarse_runner=runner,
            )
            second = run_stage_c(
                options(
                    "考虑MU",
                    new_cycle=True,
                ),
                project_root=root,
                clients=clients(),
                coarse_runner=runner,
            )
            third = run_stage_c(
                options(
                    "减少科技集中",
                    new_cycle=True,
                ),
                project_root=root,
                clients=clients(),
                coarse_runner=runner,
            )
            self.assertEqual(runner.calls, 2)
            assert second.coarse is not None
            assert third.coarse is not None
            self.assertTrue(second.coarse.reused)
            self.assertFalse(third.coarse.reused)
            revisions = list(
                first.resolution.paths
                .coarse_revisions.iterdir()
            )
            self.assertEqual(len(revisions), 2)
            self.assertTrue(
                first.resolution.paths
                .coarse_current.is_file()
            )


if __name__ == "__main__":
    unittest.main()
