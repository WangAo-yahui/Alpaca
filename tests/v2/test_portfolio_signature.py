"""验证 Stage D 输入签名会响应持仓、挂单与资本事实变化。"""

from __future__ import annotations

import unittest

from v2.models.portfolio import (
    build_capital_fingerprint,
    build_open_orders_fingerprint,
    build_positions_fingerprint,
)


class PortfolioSignatureTests(unittest.TestCase):
    def test_material_facts_change_fingerprints(
        self,
    ) -> None:
        position = {
            "symbol": "MU",
            "side": "long",
            "quantity": "10",
            "available_quantity": "10",
            "average_entry_price": "90",
        }
        self.assertNotEqual(
            build_positions_fingerprint([position]),
            build_positions_fingerprint(
                [{**position, "quantity": "11"}]
            ),
        )
        order = {
            "client_order_id": "x",
            "symbol": "MU",
            "side": "buy",
            "type": "limit",
            "quantity": "2",
            "filled_quantity": "0",
            "limit_price": "90",
            "stop_price": None,
            "status": "new",
            "extended_hours": False,
        }
        self.assertNotEqual(
            build_open_orders_fingerprint([order]),
            build_open_orders_fingerprint(
                [{**order, "limit_price": "91"}]
            ),
        )
        self.assertNotEqual(
            build_capital_fingerprint(
                {"allocatable_capital_estimate": "1000"}
            ),
            build_capital_fingerprint(
                {"allocatable_capital_estimate": "1100"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
