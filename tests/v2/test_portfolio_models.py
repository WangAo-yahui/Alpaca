"""验证 Stage D 模型的稳定指纹与禁止字段基础合同。"""

from __future__ import annotations

import unittest

from v2.models.portfolio import (
    PORTFOLIO_FORBIDDEN_OUTPUT_FIELDS,
    build_open_orders_fingerprint,
    build_positions_fingerprint,
)


class PortfolioModelTests(unittest.TestCase):
    def test_position_fingerprint_ignores_market_noise(
        self,
    ) -> None:
        first = [{
            "symbol": "MU",
            "side": "long",
            "quantity": "10",
            "available_quantity": "8",
            "average_entry_price": "90",
            "current_price": "100",
            "market_value": "1000",
        }]
        second = [{**first[0], "current_price": "120", "market_value": "1200"}]
        self.assertEqual(
            build_positions_fingerprint(first),
            build_positions_fingerprint(second),
        )

    def test_order_fingerprint_uses_remaining_quantity(
        self,
    ) -> None:
        order = {
            "client_order_id": "x",
            "symbol": "MU",
            "side": "buy",
            "type": "limit",
            "quantity": "5",
            "filled_quantity": "1",
            "limit_price": "95",
            "stop_price": None,
            "status": "new",
            "extended_hours": False,
        }
        self.assertNotEqual(
            build_open_orders_fingerprint([order]),
            build_open_orders_fingerprint(
                [{**order, "filled_quantity": "2"}]
            ),
        )
        self.assertIn(
            "quantity",
            PORTFOLIO_FORBIDDEN_OUTPUT_FIELDS,
        )


if __name__ == "__main__":
    unittest.main()
