from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v2.config import load_config
from v2.data.universe import load_universe
from tests.v2.support import copy_v2_config


class UniverseTests(unittest.TestCase):
    def test_static_universe_is_unique_and_signed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            config = load_config(
                project_root=root
            )
            first = load_universe(config)
            second = load_universe(config)
            self.assertEqual(
                len(first["symbols"]),
                len(set(first["symbols"])),
            )
            self.assertEqual(
                first["input_signature"],
                second["input_signature"],
            )
            for symbol in first["must_include"]:
                self.assertIn(
                    symbol,
                    first["symbols"],
                )

    def test_duplicate_source_symbol_is_merged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            path = root / "config/universe/etfs.json"
            payload = json.loads(
                path.read_text(encoding="utf-8")
            )
            payload["etfs"].append(
                {
                    "symbol": "SPY",
                    "enabled": True,
                }
            )
            path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            universe = load_universe(
                load_config(project_root=root)
            )
            self.assertEqual(
                universe["symbols"].count("SPY"),
                1,
            )


if __name__ == "__main__":
    unittest.main()
