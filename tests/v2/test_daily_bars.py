from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from v2.config import load_config
from v2.data.alpaca_client import AlpacaClients
from v2.data.daily_bars import (
    DailyBarStore,
    merge_daily_bars,
    update_daily_bars,
)
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
)
from tests.v2.support import copy_v2_config


UTC = ZoneInfo("UTC")


def daily_bar(
    timestamp: datetime,
    close: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=timestamp,
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        trade_count=100,
        vwap=close,
    )


class PerSymbolBarClient:
    def get_stock_bars(
        self,
        request: object,
    ) -> dict[str, list[object]]:
        symbol = getattr(
            request,
            "symbol_or_symbols",
        )
        if symbol == "FAIL":
            raise RuntimeError("temporary")
        start = datetime(
            2025,
            1,
            1,
            tzinfo=UTC,
        )
        return {
            str(symbol): [
                daily_bar(
                    start + timedelta(days=index),
                    100 + index * 0.1,
                )
                for index in range(300)
            ]
        }


class DailyBarTests(unittest.TestCase):
    def test_merge_deduplicates_and_validates(
        self,
    ) -> None:
        timestamp = datetime(
            2026,
            7,
            22,
            tzinfo=UTC,
        )
        merged, invalid = merge_daily_bars(
            [daily_bar(timestamp, 100)],
            [
                daily_bar(timestamp, 101),
                SimpleNamespace(
                    timestamp=timestamp
                    + timedelta(days=1),
                    open=100,
                    high=90,
                    low=95,
                    close=100,
                    volume=1,
                ),
            ],
            retain=300,
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0]["close"],
            101.0,
        )
        self.assertEqual(invalid, 1)

    def test_partial_symbol_failure_is_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            config = load_config(
                project_root=root
            )
            store = DailyBarStore.for_project(root)
            clients = AlpacaClients(
                trading=FakeTradingClient(),
                stock_data=PerSymbolBarClient(),
            )
            result = update_daily_bars(
                clients,
                ["MU", "FAIL"],
                config=config,
                store=store,
                now=datetime(
                    2026,
                    7,
                    23,
                    tzinfo=UTC,
                ),
            )
            self.assertEqual(
                result["success_count"],
                1,
            )
            self.assertEqual(
                result["failed_count"],
                1,
            )
            self.assertEqual(
                result["symbols"]["MU"][
                    "bar_count"
                ],
                300,
            )
            self.assertTrue(
                store.path_for("MU").exists()
            )
            self.assertTrue(
                store.path_for("FAIL").exists()
            )
            self.assertEqual(
                list(
                    store.root.glob("*.tmp")
                ),
                [],
            )

    def test_no_data_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            result = update_daily_bars(
                AlpacaClients(
                    trading=FakeTradingClient(),
                    stock_data=(
                        FakeStockDataClient()
                    ),
                ),
                ["MU"],
                config=load_config(
                    project_root=root
                ),
                now=datetime(
                    2026,
                    7,
                    23,
                    tzinfo=UTC,
                ),
            )
            self.assertEqual(
                result["no_data_count"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
