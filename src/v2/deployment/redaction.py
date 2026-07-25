"""对 Stage H 命令输出和历史日志执行确定性凭据脱敏。

作用：遮蔽 API key、secret、bearer token 以及从现有 dotenv 读取到的敏感值。
重要性：launchd、终端和日志文件都不得成为凭据或完整账户身份的旁路泄漏点。
"""

from __future__ import annotations

import re
from pathlib import Path


SENSITIVE_NAME = re.compile(
    r"(?i)(api[-_ ]?key|secret|token|password|authorization|bearer)"
)
ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_ ]?key|secret(?:[-_ ]?key)?|"
    r"token|password|authorization|bearer)"
    r"\b\s*[:=]?\s*([^\s,;]+)"
)


def dotenv_secret_values(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    values: list[str] = []
    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if not SENSITIVE_NAME.search(name):
            continue
        normalized = value.strip().strip("\"'")
        if normalized:
            values.append(normalized)
    return tuple(
        sorted(set(values), key=len, reverse=True)
    )


def redact_text(
    text: str,
    *,
    secret_values: tuple[str, ...] = (),
) -> str:
    result = str(text)
    for value in secret_values:
        if value:
            result = result.replace(value, "[REDACTED]")
    result = ASSIGNMENT.sub(
        lambda match: (
            f"{match.group(1)}=[REDACTED]"
        ),
        result,
    )
    return result
