from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from v2.exceptions import ConfigurationError
from v2.releases import load_strategy_release


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StrategyReleaseTests(unittest.TestCase):
    def test_manifest_hashes_and_mutation_detection(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.0.0",
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(len(release.release_hash), 64)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(
                PROJECT_ROOT / "strategies",
                root / "strategies",
            )
            prompt = (
                root
                / "strategies/core_long/1.0.0"
                / "prompts/coarse.md"
            )
            prompt.write_text(
                prompt.read_text(encoding="utf-8")
                + "\nmodified\n",
                encoding="utf-8",
            )
            with self.assertRaises(
                ConfigurationError
            ) as caught:
                load_strategy_release(
                    "core_long",
                    "1.0.0",
                    project_root=root,
                )
            self.assertEqual(
                caught.exception.code,
                "STRATEGY_RELEASE_HASH_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
