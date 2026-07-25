from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from v2.data.quotes import normalize_quote


UTC = ZoneInfo("UTC")


class QuoteNormalizationTests(unittest.TestCase):
    def test_no_quote_is_not_zero_price(self) -> None:
        result = normalize_quote("MU", None)
        self.assertEqual(
            result["status"],
            "no_data",
        )
        self.assertIsNone(result["midpoint"])
        self.assertIsNone(result["bid_price"])

    def test_spread_and_age(self) -> None:
        result = normalize_quote(
            "MU",
            SimpleNamespace(
                bid_price="99",
                bid_size="10",
                ask_price="101",
                ask_size="11",
                timestamp=(
                    "2026-07-23T14:00:00+00:00"
                ),
            ),
            now=datetime(
                2026,
                7,
                23,
                14,
                0,
                5,
                tzinfo=UTC,
            ),
        )
        self.assertEqual(
            result["midpoint"],
            100.0,
        )
        self.assertEqual(result["spread"], 2.0)
        self.assertEqual(
            result["spread_bps"],
            200.0,
        )
        self.assertEqual(
            result["quote_age_seconds"],
            5.0,
        )

    def test_crossed_quote_is_invalid(self) -> None:
        result = normalize_quote(
            "MU",
            SimpleNamespace(
                bid_price=101,
                bid_size=1,
                ask_price=100,
                ask_size=1,
                timestamp=(
                    "2026-07-23T14:00:00+00:00"
                ),
            ),
        )
        self.assertEqual(
            result["status"],
            "invalid_data",
        )


if __name__ == "__main__":
    unittest.main()
