"""验证 Stage G 写后即时对账与下一轮触发原因。

作用：覆盖账户资本、open、filled、reject 和 tracked order 去重。
重要性：cycle 终态必须来自券商最新事实，而不是 submit 返回值。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import (
    SubmissionOperationState,
)
from v2.runtime import (
    build_cycle_paths,
    ensure_cycle_directories,
)
from v2.trading.reconciliation import (
    maintain_previous_submissions,
    reconcile_submission,
)
from v2.trading.submission_journal import SubmissionJournal
from tests.v2.fakes import fake_order
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
    operation,
)


class ReconciliationTests(unittest.TestCase):
    def test_open_order_is_normal(self) -> None:
        trading = WriteTradingClient()
        broker = fake_order(
            "MU",
            id="broker-1",
            client_order_id="wa2-paper1-test-1",
            status="new",
            filled_qty="0",
            filled_avg_price=None,
        )
        trading.add_order(broker)
        item = operation()
        item.state = SubmissionOperationState.COMPLETED
        item.broker_order_id = "broker-1"
        result = reconcile_submission(
            clients=clients_for(trading),
            profile_id="paper1",
            cycle_id="20260724T140000",
            operations=[item],
        )
        self.assertEqual(result["summary"]["open"], 1)
        self.assertFalse(
            result["requires_next_cycle_rebalance"]
        )

    def test_reject_requires_rebalance(self) -> None:
        trading = WriteTradingClient()
        trading.add_order(
            fake_order(
                "MU",
                id="broker-1",
                client_order_id="wa2-paper1-test-1",
                status="rejected",
                filled_qty="0",
                filled_avg_price=None,
            )
        )
        item = operation()
        item.broker_order_id = "broker-1"
        result = reconcile_submission(
            clients=clients_for(trading),
            profile_id="paper1",
            cycle_id="20260724T140000",
            operations=[item],
        )
        self.assertEqual(result["summary"]["rejected"], 1)
        self.assertIn(
            "broker_order_rejected", result["reasons"]
        )

    def test_capital_is_refetched(self) -> None:
        result = reconcile_submission(
            clients=clients_for(WriteTradingClient()),
            profile_id="paper1",
            cycle_id="20260724T140000",
            operations=[],
        )
        self.assertEqual(result["capital"]["cash"], 10000.5)
        self.assertEqual(
            result["capital"]["buying_power"], 18000.25
        )

    def test_startup_maintains_prior_day_open_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = build_cycle_paths(
                cycle_id="20260723T140000",
                run_date="2026-07-23",
                project_root=root,
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.2.0",
            )
            ensure_cycle_directories(paths)
            trading = WriteTradingClient()
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    client_order_id="wa2-paper1-test-1",
                    status="new",
                    filled_qty="0",
                    filled_avg_price=None,
                )
            )
            item = operation()
            item.state = SubmissionOperationState.COMPLETED
            item.broker_order_id = "broker-1"
            item.broker_status = "new"
            SubmissionJournal.load_or_create(
                paths.submission_journal,
                profile_id="paper1",
                run_date="2026-07-23",
                cycle_id="20260723T140000",
                operations=[item],
            )
            maintained = maintain_previous_submissions(
                clients=clients_for(trading),
                project_root=root,
                run_date="2026-07-24",
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.2.0",
            )
            self.assertEqual(
                maintained, ["20260723T140000"]
            )
            self.assertTrue(paths.reconciliation.is_file())


if __name__ == "__main__":
    unittest.main()
