"""验证 core_long 1.2.0 新增 execution 能力且旧工件字节不变。

作用：校验 manifest 哈希并逐项比较 1.1.0 的 coarse/portfolio 文件。
重要性：发布必须通过新版本演进，不能原地篡改历史决策合同。
"""

from __future__ import annotations

import unittest

from v2.profiles import load_profile
from v2.releases import load_strategy_release


class StrategyOneTwoReleaseTests(unittest.TestCase):
    def test_execution_release_and_immutability(
        self,
    ) -> None:
        prior = load_strategy_release(
            "core_long",
            "1.1.0",
        )
        current = load_strategy_release(
            "core_long",
            "1.2.0",
        )
        self.assertEqual(
            load_profile(
                "paper1"
            ).strategy_version,
            "1.2.0",
        )
        self.assertIn(
            "prompts/execution.md",
            current.prompt_hashes,
        )
        self.assertIn(
            "schemas/execution_output.schema.json",
            current.schema_hashes,
        )
        self.assertIn(
            "config/execution_policy.json",
            current.config_hashes,
        )
        for relative in (
            "prompts/coarse.md",
            "prompts/coarse_AGENTS.md",
            "prompts/portfolio.md",
            "prompts/portfolio_AGENTS.md",
            "schemas/coarse_output.schema.json",
            "schemas/portfolio_output.schema.json",
            "config/coarse_policy.json",
            "config/portfolio_policy.json",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(
                    (prior.root / relative).read_bytes(),
                    (
                        current.root / relative
                    ).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
