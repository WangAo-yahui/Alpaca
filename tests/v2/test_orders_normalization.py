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

    def test_advanced_order_legs_and_trailing_fields(
        self,
    ) -> None:
        order = normalize_order(
            fake_order(
                order_class="oco",
                trail_percent="5",
                hwm="111.25",
                legs=[
                    fake_order(
                        side="sell",
                        type="stop_limit",
                        stop_price="90",
                        limit_price="89",
                        order_class="oco",
                        legs=[],
                    )
                ],
            )
        )
        self.assertEqual(
            order["order_class"],
            "oco",
        )
        self.assertEqual(
            order["trail_percent"],
            5.0,
        )
        self.assertEqual(
            order["high_water_mark"],
            111.25,
        )
        self.assertEqual(
            order["legs"][0]["stop_price"],
            90.0,
        )


if __name__ == "__main__":
    unittest.main()
