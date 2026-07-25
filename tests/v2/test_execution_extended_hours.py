"""验证扩展时段只能形成券商支持的带价格 limit intent。

作用：覆盖 before-market 合法路径、market intent 和 unsupported 拒绝。
重要性：扩展时段流动性与成交方式风险高于 regular session。
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.models.execution import (
    validate_execution_output,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    stage_e_fixture,
    valid_execution_output,
)


class ExecutionExtendedHoursTests(unittest.TestCase):
    def test_limit_and_capability_rules(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution
                .input_result.payload
            )
            source["execution_snapshot"][
                "market_phase"
            ] = "before_market_open"
            output = valid_execution_output(source)
            schema = load_json_object(
                load_strategy_release(
                    "core_long",
                    "1.2.0",
                    project_root=root,
                ).root
                / "schemas/execution_output.schema.json"
            )
            self.assertTrue(
                validate_execution_output(
                    output,
                    input_payload=source,
                    schema=schema,
                ).valid
            )
            market = copy.deepcopy(output)
            market["decisions"][0][
                "order_intent"
            ]["preferred_type"] = "market"
            codes = {
                item["code"]
                for item in validate_execution_output(
                    market,
                    input_payload=source,
                    schema=schema,
                ).errors
            }
            self.assertIn(
                "EXTENDED_HOURS_INTENT_INVALID",
                codes,
            )
            unsupported = copy.deepcopy(source)
            unsupported[
                "execution_snapshot"
            ][
                "broker_extended_hours_capability"
            ]["supported"] = False
            codes = {
                item["code"]
                for item in validate_execution_output(
                    output,
                    input_payload=unsupported,
                    schema=schema,
                ).errors
            }
            self.assertIn(
                "EXTENDED_HOURS_UNSUPPORTED",
                codes,
            )


if __name__ == "__main__":
    unittest.main()
