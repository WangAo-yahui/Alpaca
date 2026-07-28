"""验证 Stage F 最终订单前快照。

作用：检查快照时序、Decimal 字符串和关键账户刷新失败。
重要性：旧 execution 快照不得被误当成可提交订单的最新事实。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from alpaca.data.enums import DataFeed

from v2.data.alpaca_client import AlpacaClients
from v2.data.pretrade_snapshot import (
    create_pretrade_snapshot,
)
from v2.profiles import load_risk_profile
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_account,
    fake_position,
)
from tests.v2.order_support import (
    GENERATED_AT,
    execution_output,
    order_configs,
    order_paths,
)


class PreTradeSnapshotTests(unittest.TestCase):
    def _live_risk(self):
        return load_risk_profile(
            "live_full@1.1.0",
            project_root=(
                Path(__file__).resolve().parents[2]
            ),
        )

    @staticmethod
    def _quote(
        bid: str,
        ask: str,
        *,
        seconds: int,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            bid_price=bid,
            ask_price=ask,
            bid_size="40",
            ask_size="80",
            bid_exchange="V",
            ask_exchange="V",
            timestamp=(
                GENERATED_AT
                + timedelta(seconds=seconds)
            ),
        )

    def _clients(self, *, failures=None):
        quote = SimpleNamespace(
            bid_price="100.00",
            ask_price="100.10",
            bid_size="10",
            ask_size="10",
            timestamp=GENERATED_AT,
        )
        return AlpacaClients(
            trading=FakeTradingClient(
                positions=[
                    fake_position("MU")
                ],
                failures=failures,
            ),
            stock_data=FakeStockDataClient(
                quotes={"MU": quote}
            ),
        )

    def test_snapshot_is_after_execution_and_decimalized(
        self,
    ) -> None:
        risk, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            paths = order_paths(Path(temp))
            result = create_pretrade_snapshot(
                paths,
                self._clients(),
                execution_output=execution_output(),
                order_policy=policy,
                risk_profile=risk,
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
            )
            self.assertTrue(
                paths.pretrade_snapshot.is_file()
            )
        self.assertTrue(
            result.order_planning_ready
        )
        self.assertIsInstance(
            result.payload["account"]["cash"],
            str,
        )
        self.assertIsInstance(
            result.payload["quotes"]["MU"][
                "ask_price"
            ],
            str,
        )
        self.assertGreater(
            result.payload["retrieved_at"],
            result.payload[
                "execution_generated_at"
            ],
        )

    def test_critical_account_failure_blocks_planning(
        self,
    ) -> None:
        risk, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                self._clients(
                    failures={"account"}
                ),
                execution_output=execution_output(),
                order_policy=policy,
                risk_profile=risk,
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
            )
        self.assertFalse(
            result.order_planning_ready
        )
        self.assertGreater(
            result.payload["data_quality"][
                "critical_error_count"
            ],
            0,
        )

    def test_sunday_overnight_refresh_uses_overnight_feed(
        self,
    ) -> None:
        risk, policy = order_configs()
        clients = self._clients()
        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                clients,
                execution_output=execution_output(),
                order_policy=policy,
                risk_profile=risk,
                now=GENERATED_AT.replace(
                    year=2026,
                    month=7,
                    day=27,
                    hour=2,
                ),
                is_market_holiday=False,
            )
        self.assertEqual(
            result.payload["market_phase"],
            "overnight_session",
        )
        self.assertEqual(
            clients.stock_data.quote_requests[-1].feed,
            DataFeed.OVERNIGHT,
        )

    def test_iex_wide_spread_requires_three_fresh_passes(
        self,
    ) -> None:
        class SequencedQuotes(FakeStockDataClient):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.responses = [
                    self._quote(
                        "390.00",
                        "393.08",
                        seconds=1,
                    ),
                    *[
                        self._quote(
                            "390.00",
                            "393.08",
                            seconds=seconds,
                        )
                        for seconds in range(
                            2,
                            12,
                        )
                    ],
                    *[
                        self._quote(
                            f"392.{82 + index}",
                            f"392.{90 + index}",
                            seconds=12 + index,
                        )
                        for index in range(3)
                    ],
                    self._quote(
                        "392.85",
                        "392.93",
                        seconds=15,
                    ),
                ]

            def get_stock_latest_quote(
                inner_self,
                request: object,
            ) -> dict[str, object]:
                inner_self.quote_requests.append(
                    request
                )
                index = min(
                    len(
                        inner_self.quote_requests
                    )
                    - 1,
                    len(inner_self.responses) - 1,
                )
                return {
                    "MU": inner_self.responses[index]
                }

        class CountingTrading(
            FakeTradingClient
        ):
            def __init__(inner_self) -> None:
                super().__init__(
                    positions=[
                        fake_position("MU")
                    ]
                )
                inner_self.account_calls = 0
                inner_self.position_calls = 0
                inner_self.order_calls = 0

            def get_account(inner_self):
                inner_self.account_calls += 1
                if inner_self.account_calls == 2:
                    return fake_account(
                        cash="9999.25"
                    )
                return super().get_account()

            def get_all_positions(inner_self):
                inner_self.position_calls += 1
                return super().get_all_positions()

            def get_orders(
                inner_self,
                *,
                filter: object,
            ):
                inner_self.order_calls += 1
                return super().get_orders(
                    filter=filter
                )

        _, policy = order_configs()
        stock_data = SequencedQuotes()
        trading = CountingTrading()
        clients = AlpacaClients(
            trading=trading,
            stock_data=stock_data,
        )
        clock_seconds = [1]

        def clock():
            clock_seconds[0] += 1
            return (
                GENERATED_AT
                + timedelta(
                    seconds=clock_seconds[0]
                )
            )

        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                clients,
                execution_output=execution_output(),
                order_policy=policy,
                risk_profile=self._live_risk(),
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
                sleep_func=sleeps.append,
                now_func=clock,
            )

        quote = result.payload["quotes"]["MU"]
        recheck = quote["spread_recheck"]
        self.assertTrue(
            result.order_planning_ready
        )
        self.assertEqual(
            quote["data_feed"],
            "iex",
        )
        self.assertEqual(
            quote["bid_exchange"],
            "V",
        )
        self.assertEqual(
            recheck["status"],
            "passed",
        )
        self.assertEqual(
            recheck[
                "observed_consecutive_passes"
            ],
            3,
        )
        self.assertEqual(
            len(stock_data.quote_requests),
            15,
        )
        self.assertEqual(
            sleeps,
            [0.5] * 13,
        )
        self.assertTrue(
            all(
                request.feed == DataFeed.IEX
                for request
                in stock_data.quote_requests
            )
        )
        self.assertEqual(
            result.payload["account"]["cash"],
            "9999.25",
        )
        self.assertEqual(
            trading.account_calls,
            2,
        )
        self.assertEqual(
            trading.position_calls,
            2,
        )
        self.assertEqual(
            trading.order_calls,
            4,
        )

    def test_duplicate_narrow_quote_does_not_pass_recheck(
        self,
    ) -> None:
        class RepeatedQuotes(FakeStockDataClient):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.calls = 0
                inner_self.wide = self._quote(
                    "390.00",
                    "393.08",
                    seconds=1,
                )
                inner_self.narrow = self._quote(
                    "392.82",
                    "392.90",
                    seconds=3,
                )

            def get_stock_latest_quote(
                inner_self,
                request: object,
            ) -> dict[str, object]:
                inner_self.quote_requests.append(
                    request
                )
                inner_self.calls += 1
                return {
                    "MU": (
                        inner_self.wide
                        if inner_self.calls == 1
                        else inner_self.narrow
                    )
                }

        _, policy = order_configs()
        clients = AlpacaClients(
            trading=FakeTradingClient(
                positions=[fake_position("MU")]
            ),
            stock_data=RepeatedQuotes(),
        )
        clock_seconds = [1]

        def clock():
            clock_seconds[0] += 1
            return (
                GENERATED_AT
                + timedelta(
                    seconds=clock_seconds[0]
                )
            )

        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                clients,
                execution_output=execution_output(),
                order_policy=policy,
                risk_profile=self._live_risk(),
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
                sleep_func=lambda _: None,
                now_func=clock,
            )

        quote = result.payload["quotes"]["MU"]
        self.assertFalse(
            result.order_planning_ready
        )
        self.assertEqual(
            quote["status"],
            "unstable_data",
        )
        self.assertEqual(
            quote["spread_recheck"]["status"],
            "failed",
        )
        self.assertEqual(
            quote["spread_recheck"][
                "observed_consecutive_passes"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
