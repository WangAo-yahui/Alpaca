"""验证 Stage F 计划与 client order ID。

作用：覆盖稳定性、碰撞隔离、字符安全与长度上限。
重要性：幂等 ID 是 Stage G 防止恢复和重试重复下单的核心键。
"""

from __future__ import annotations

import unittest

from v2.trading.idempotency import (
    build_client_order_id,
    build_plan_id,
)


class OrderIdempotencyTests(unittest.TestCase):
    def _values(self, **overrides):
        values = {
            "profile_id": "paper1",
            "strategy_id": "core_long",
            "strategy_version": "1.2.0",
            "cycle_id": "20260724T140000",
            "symbol": "MU",
            "side": "buy",
            "intent_index": 0,
            "order_role": "primary",
            "idempotency_version": "1",
        }
        values.update(overrides)
        return values

    def test_same_input_is_stable(self) -> None:
        values = self._values()
        self.assertEqual(
            build_plan_id(**values),
            build_plan_id(**values),
        )
        self.assertEqual(
            build_client_order_id(**values),
            build_client_order_id(**values),
        )

    def test_different_intents_do_not_collide(self) -> None:
        first = build_client_order_id(
            **self._values(intent_index=0)
        )
        second = build_client_order_id(
            **self._values(intent_index=1)
        )
        self.assertNotEqual(first, second)

    def test_id_is_broker_safe_and_has_no_account(self) -> None:
        result = build_client_order_id(
            **self._values(),
            max_length=48,
        )
        self.assertTrue(result.startswith("wa2-"))
        self.assertLessEqual(len(result), 48)
        self.assertNotIn("account", result)


if __name__ == "__main__":
    unittest.main()
