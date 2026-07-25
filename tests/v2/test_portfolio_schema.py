"""验证 1.1.0 portfolio Schema 严格且拒绝开放对象。"""

from __future__ import annotations

import unittest

from v2.codex.validation import preflight_output_schema
from v2.exceptions import ConfigurationError
from v2.releases import load_strategy_release
from v2.runtime import load_json_object


class PortfolioSchemaTests(unittest.TestCase):
    def test_release_schema_passes_preflight(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.1.0",
        )
        preflight_output_schema(
            load_json_object(
                release.root
                / "schemas"
                / "portfolio_output.schema.json"
            )
        )

    def test_open_object_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ConfigurationError):
            preflight_output_schema(
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
