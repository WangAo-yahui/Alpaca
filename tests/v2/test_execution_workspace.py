"""验证 Stage E Codex 工作区只包含当前阶段合同。

作用：检查四份输入副本、固定 prompt/schema/policy 和风险配置。
重要性：凭据、账户绑定及其他 profile 不得进入 Codex 沙箱。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.support import stage_e_fixture


class ExecutionWorkspaceTests(unittest.TestCase):
    def test_workspace_contract_has_no_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = stage_e_fixture(Path(temp))
            root = (
                result.resolution.paths
                .execution_workspace
            )
            for relative in (
                "data/execution_input.json",
                "data/initial_guidance.json",
                "data/user_review.json",
                "data/portfolio_output.json",
                "data/execution_snapshot.json",
                "config/execution_policy.json",
                "config/risk_profile.json",
                "prompts/execution.md",
                "schemas/execution_output.schema.json",
                "AGENTS.md",
            ):
                self.assertTrue(
                    (root / relative).is_file(),
                    relative,
                )
            text = "\n".join(
                path.read_text(
                    encoding="utf-8"
                )
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(
                "ALPACA_SECRET_KEY",
                text,
            )


if __name__ == "__main__":
    unittest.main()
