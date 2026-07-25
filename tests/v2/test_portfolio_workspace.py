"""验证组合 Codex 工作区仅包含 Stage D 必要、无凭据的研究材料。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.main import run_stage_d
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class PortfolioWorkspaceTests(unittest.TestCase):
    def test_workspace_contract_and_no_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            result = run_stage_d(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=FakePortfolioRunner(),
            )
            workspace = (
                result.resolution.paths
                .portfolio_workspace
            )
            expected = {
                "AGENTS.md",
                "data/portfolio_input.json",
                "data/initial_guidance.json",
                "data/coarse_output.json",
                "data/base_snapshot.json",
                "data/market/context.json",
                "config/portfolio_policy.json",
                "config/risk_profile.json",
                "prompts/portfolio.md",
                "schemas/portfolio_output.schema.json",
            }
            actual = {
                path.relative_to(
                    workspace
                ).as_posix()
                for path in workspace.rglob("*")
                if path.is_file()
                and ".tmp" not in path.parts
            }
            self.assertEqual(expected, actual)
            combined = "\n".join(
                path.read_text(encoding="utf-8")
                for path in workspace.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(
                "ALPACA_PAPER2_SECRET",
                combined,
            )
            self.assertFalse(
                (workspace / ".env").exists()
            )


if __name__ == "__main__":
    unittest.main()
