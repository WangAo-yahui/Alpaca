from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.guidance import collect_initial_guidance
from v2.runtime import (
    build_cycle_paths,
    ensure_cycle_directories,
)


class TTY:
    def isatty(self) -> bool:
        return True


class InitialGuidancePromptTests(unittest.TestCase):
    def test_prompt_and_reviewed_empty_modes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for suffix, answer, mode in (
                ("00", "偏向低估值", "prompt"),
                (
                    "01",
                    "  ",
                    "reviewed_no_comment",
                ),
            ):
                paths = build_cycle_paths(
                    run_date="2026-07-24",
                    cycle_id=(
                        "20260724T1100" + suffix
                    ),
                    project_root=root,
                    profile_id="paper1",
                )
                ensure_cycle_directories(paths)
                result = collect_initial_guidance(
                    parse_cli_args(
                        ["--profile", "paper1"]
                    ),
                    paths,
                    input_func=lambda _: answer,
                    stdin=TTY(),  # type: ignore[arg-type]
                )
                self.assertEqual(result.mode, mode)


if __name__ == "__main__":
    unittest.main()
