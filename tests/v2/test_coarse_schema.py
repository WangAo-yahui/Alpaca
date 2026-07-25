from __future__ import annotations

import unittest

from v2.codex.validation import (
    load_coarse_schema,
    preflight_output_schema,
)
from v2.exceptions import ConfigurationError
from tests.v2.support import (
    PROJECT_ROOT,
)


class CoarseSchemaTests(unittest.TestCase):
    def test_repository_schema_passes_preflight(
        self,
    ) -> None:
        schema = load_coarse_schema(
            PROJECT_ROOT
            / "schemas/v2/coarse_output.schema.json"
        )
        preflight_output_schema(schema)

    def test_preflight_rejects_open_object_and_union(
        self,
    ) -> None:
        schemas = [
            {
                "type": "object",
                "properties": {
                    "value": {"type": "string"}
                },
                "required": ["value"],
            },
            {
                "oneOf": [
                    {"type": "string"},
                    {"type": "number"},
                ]
            },
        ]
        for schema in schemas:
            with self.subTest(schema=schema):
                with self.assertRaises(
                    ConfigurationError
                ):
                    preflight_output_schema(
                        schema
                    )


if __name__ == "__main__":
    unittest.main()
