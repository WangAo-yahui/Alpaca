"""验证 core_long 1.2.0 execution Schema 是严格封闭对象。

作用：执行通用 Schema 预检并确认 decision 拒绝额外属性。
重要性：开放对象可能让数量或实际订单字段绕过契约。
"""

from __future__ import annotations

import unittest

from v2.codex.validation import (
    preflight_output_schema,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object


class ExecutionSchemaTests(unittest.TestCase):
    def test_release_schema_is_strict(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.2.0",
        )
        schema = load_json_object(
            release.root
            / "schemas/execution_output.schema.json"
        )
        preflight_output_schema(schema)
        self.assertFalse(
            schema["properties"]["decisions"][
                "items"
            ]["additionalProperties"]
        )


if __name__ == "__main__":
    unittest.main()
