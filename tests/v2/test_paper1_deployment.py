"""验证 Stage E 只部署 paper1 且只读取它的凭据变量。

作用：检查 profile、strategy 和客户端工厂收到的环境值。
重要性：避免扫描或误用未启用 paper2、paper3 的凭据。
"""

from __future__ import annotations

import unittest

from v2.data.alpaca_client import (
    create_alpaca_clients,
)
from v2.profiles import load_profile


class PaperOneDeploymentTests(unittest.TestCase):
    def test_only_paper1_credentials_and_release(
        self,
    ) -> None:
        profile = load_profile("paper1")
        self.assertEqual(
            profile.strategy_version,
            "1.2.0",
        )
        captured = []

        def factory(**kwargs):
            captured.append(kwargs)
            return object()

        create_alpaca_clients(
            profile=profile,
            environ={
                "ALPACA_API_KEY": "paper1-key",
                "ALPACA_SECRET_KEY": "paper1-secret",
                "ALPACA_PAPER2_API_KEY": "wrong",
                "ALPACA_PAPER2_SECRET_KEY": "wrong",
            },
            trading_factory=factory,
            stock_data_factory=factory,
        )
        self.assertTrue(
            all(
                item["api_key"] == "paper1-key"
                and item["secret_key"]
                == "paper1-secret"
                for item in captured
            )
        )


if __name__ == "__main__":
    unittest.main()
