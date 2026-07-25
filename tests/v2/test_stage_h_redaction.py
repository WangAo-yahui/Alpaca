"""验证 Stage H 日志对 dotenv 与通用敏感字段脱敏。

作用：确保真实值、Bearer 和 secret 赋值不会出现在读取或写入的日志。
重要性：凭据不得因部署诊断、launchd 输出或 logs 命令进入持久化文件。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.deployment.redaction import (
    dotenv_secret_values,
    redact_text,
)


class StageHRedactionTests(unittest.TestCase):
    def test_dotenv_values_and_assignments_are_hidden(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(
                "ALPACA_API_KEY=public-key-value\n"
                "ALPACA_SECRET_KEY=secret-value\n"
                "SAFE_SETTING=true\n",
                encoding="utf-8",
            )
            values = dotenv_secret_values(path)
            redacted = redact_text(
                "api_key=public-key-value "
                "secret=secret-value "
                "Authorization: Bearer abc",
                secret_values=values,
            )
            self.assertNotIn(
                "public-key-value", redacted
            )
            self.assertNotIn("secret-value", redacted)
            self.assertNotIn("Bearer abc", redacted)
            self.assertIn("[REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main()
