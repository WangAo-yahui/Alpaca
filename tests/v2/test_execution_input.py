"""验证第三阶段签名输入包含四类必要决策资料。

作用：检查 guidance、review、portfolio、execution snapshot 和风险边界。
重要性：缺少任一来源都会破坏执行判断的可审计性。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.support import stage_e_fixture


class ExecutionInputTests(unittest.TestCase):
    def test_required_inputs_and_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = stage_e_fixture(Path(temp))
            assert result.execution is not None
            payload = (
                result.execution
                .input_result.payload
            )
            for key in (
                "initial_guidance",
                "user_review",
                "portfolio",
                "execution_snapshot",
                "risk_profile",
                "execution_policy",
                "trade_permission",
                "data_quality",
            ):
                self.assertIn(key, payload)
            self.assertEqual(
                payload["profile"]["profile_id"],
                "paper1",
            )
            self.assertEqual(
                len(payload["input_signature"]),
                64,
            )


if __name__ == "__main__":
    unittest.main()
