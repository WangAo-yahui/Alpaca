from __future__ import annotations

import unittest

from v2.data.orders import (
    normalize_order,
    normalize_orders,
    system_submitted_orders,
)
from tests.v2.fakes import fake_order


class OrderNormalizationTests(unittest.TestCase):
    def test_empty_orders(self) -> None:
        self.assertEqual(
            normalize_orders([]),
            [],
        )

    def test_extended_order_fields(self) -> None:
        order = normalize_order(fake_order())
        self.assertEqual(order["symbol"], "MU")
        self.assertEqual(order["quantity"], 5.0)
        self.assertEqual(
            order["filled_quantity"],
            1.0,
        )
        self.assertTrue(order["extended_hours"])

    def test_filters_system_orders(self) -> None:
        orders = normalize_orders(
            [
                fake_order(
                    "MU",
                    client_order_id="wa2-MU-buy-0",
                ),
                fake_order(
                    "NVDA",
                    client_order_id="manual-order",
                ),
            ]
        )
        result = system_submitted_orders(orders)
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["symbol"],
            "MU",
        )


if __name__ == "__main__":
    unittest.main()
