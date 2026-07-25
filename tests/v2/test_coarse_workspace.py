from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.codex.workspace import (
    prepare_coarse_workspace,
)
from v2.config import load_config
from v2.runtime import build_daily_paths
from tests.v2.support import (
    prepare_stage_c_project,
)


class CoarseWorkspaceTests(unittest.TestCase):
    def test_workspace_contains_only_stage_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            config = load_config(
                project_root=root
            )
            paths = build_daily_paths(
                "2026-07-23",
                project_root=root,
            )
            workspace = prepare_coarse_workspace(
                paths,
                config=config,
                input_payload={
                    "schema_version": "1.0",
                    "stage": "coarse_selection",
                },
            )
            relative_files = {
                path.relative_to(workspace.root)
                for path in workspace.root.rglob(
                    "*"
                )
                if path.is_file()
            }
            self.assertEqual(
                relative_files,
                {
                    Path("AGENTS.md"),
                    Path("data/input.json"),
                    Path(
                        "config/coarse_policy.json"
                    ),
                    Path("prompts/coarse.md"),
                    Path(
                        "schemas/"
                        "coarse_output.schema.json"
                    ),
                },
            )
            agents = workspace.agents.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                ".tmp/codex/",
                agents,
            )
            self.assertIn(".env", agents)


if __name__ == "__main__":
    unittest.main()
