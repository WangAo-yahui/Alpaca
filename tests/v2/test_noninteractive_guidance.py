from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.exceptions import ConfigurationError
from v2.guidance import collect_initial_guidance
from v2.runtime import (
    build_cycle_paths,
    ensure_cycle_directories,
)


class NoninteractiveGuidanceTests(unittest.TestCase):
    def test_non_tty_requires_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = build_cycle_paths(
                run_date="2026-07-24",
                cycle_id="20260724T120000",
                project_root=Path(temp),
                profile_id="paper2",
            )
            ensure_cycle_directories(paths)
            with self.assertRaises(
                ConfigurationError
            ) as caught:
                collect_initial_guidance(
                    parse_cli_args(
                        ["--profile", "paper2"]
                    ),
                    paths,
                    stdin=io.StringIO(),
                )
            self.assertEqual(
                caught.exception.code,
                "INITIAL_GUIDANCE_REQUIRED_NONINTERACTIVE",
            )
            self.assertIn(
                "--unattended",
                str(caught.exception),
            )


if __name__ == "__main__":
    unittest.main()
