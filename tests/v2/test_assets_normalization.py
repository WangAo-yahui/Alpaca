from __future__ import annotations

import unittest

from v2.data.alpaca_client import AlpacaClients
from v2.data.assets import (
    AssetCache,
    normalize_asset,
)
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_asset,
)


class AssetNormalizationTests(unittest.TestCase):
    def test_asset_fields(self) -> None:
        result = normalize_asset(
            fake_asset("mu")
        )
        self.assertEqual(result["symbol"], "MU")
        self.assertTrue(result["tradable"])
        self.assertEqual(
            result["exchange"],
            "NASDAQ",
        )

    def test_cache_avoids_duplicate_api_calls(
        self,
    ) -> None:
        trading = FakeTradingClient()
        cache = AssetCache(
            AlpacaClients(
                trading=trading,
                stock_data=FakeStockDataClient(),
            )
        )
        first = cache.get("MU")
        second = cache.get("mu")
        self.assertEqual(first, second)
        self.assertEqual(
            trading.asset_calls,
            ["MU"],
        )

    def test_partial_asset_failure_is_reported(
        self,
    ) -> None:
        trading = FakeTradingClient(
            failures={"asset:TSLA"}
        )
        cache = AssetCache(
            AlpacaClients(
                trading=trading,
                stock_data=FakeStockDataClient(),
            )
        )
        result = cache.get_many(["MU", "TSLA"])
        self.assertIn("MU", result.assets)
        self.assertNotIn("TSLA", result.assets)
        self.assertEqual(len(result.errors), 1)


if __name__ == "__main__":
    unittest.main()
