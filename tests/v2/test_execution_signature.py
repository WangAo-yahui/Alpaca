"""验证 execution signature 对相同事实稳定、对新快照敏感。

作用：直接重建 Stage E 输入并改变 snapshot 的报价事实。
重要性：旧执行输出不能在行情变化后被错误复用。
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.config import load_config
from v2.models.execution import (
    build_execution_input,
)
from v2.profiles import load_risk_profile
from v2.releases import load_strategy_release
from tests.v2.support import stage_e_fixture


class ExecutionSignatureTests(unittest.TestCase):
    def test_snapshot_change_changes_signature(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = (
                result.execution
                .input_result.payload
            )
            release = load_strategy_release(
                "core_long",
                "1.2.0",
                project_root=root,
            )
            risk = load_risk_profile(
                "paper_standard@1.0.0",
                project_root=root,
            )
            config = load_config(
                project_root=root
            )

            def build(snapshot):
                return build_execution_input(
                    paths=(
                        result.resolution.paths
                    ),
                    state=(
                        result.resolution.state
                    ),
                    initial_guidance=source[
                        "initial_guidance"
                    ],
                    user_review=source[
                        "user_review"
                    ],
                    portfolio_output=source[
                        "portfolio"
                    ],
                    execution_snapshot=snapshot,
                    risk_profile=risk,
                    risk_limits=config.risk,
                    execution_policy=source[
                        "execution_policy"
                    ],
                    release=release,
                )

            first = build(
                source["execution_snapshot"]
            )
            second = build(
                source["execution_snapshot"]
            )
            changed = copy.deepcopy(
                source["execution_snapshot"]
            )
            changed["market_phase"] = "unknown"
            third = build(changed)
            self.assertEqual(
                first.input_signature,
                second.input_signature,
            )
            self.assertNotEqual(
                first.input_signature,
                third.input_signature,
            )


if __name__ == "__main__":
    unittest.main()
