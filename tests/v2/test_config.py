from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v2.config import load_config
from v2.exceptions import ConfigurationError
from tests.v2.support import copy_v2_config


class ConfigTests(unittest.TestCase):
    def test_loads_all_six_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            first = load_config(project_root=root)
            second = load_config(project_root=root)
            self.assertEqual(len(first.documents), 6)
            self.assertEqual(
                first.config_version,
                "2026-07-23-stage-b-v1",
            )
            self.assertEqual(
                first.signature,
                second.signature,
            )
            self.assertEqual(len(first.signature), 64)

    def test_rejects_disabling_live_profile_support(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            path = root / "config/v2/system.json"
            payload = json.loads(
                path.read_text(encoding="utf-8")
            )
            payload["allow_live"] = False
            path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaises(
                ConfigurationError
            ):
                load_config(project_root=root)

    def test_rejects_candidate_count_not_sixty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            path = root / "config/v2/stages.json"
            payload = json.loads(
                path.read_text(encoding="utf-8")
            )
            payload["coarse_candidate_count"] = 59
            path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaises(
                ConfigurationError
            ):
                load_config(project_root=root)

    def test_rejects_mixed_config_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            path = root / "config/v2/risk.json"
            payload = json.loads(
                path.read_text(encoding="utf-8")
            )
            payload["config_version"] = "different"
            path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaises(
                ConfigurationError
            ):
                load_config(project_root=root)


if __name__ == "__main__":
    unittest.main()
