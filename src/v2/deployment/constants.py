"""定义 Stage H 运维命令共享的固定身份与退出码。

作用：为 launchd、人工命令和健康检查提供同一套稳定数值合同。
重要性：调用方只有依赖稳定退出码，才能把 no-action、风险阻止和部署失败正确区分。
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    ALREADY_RUNNING = 10
    NO_ACTION = 20
    CONFIGURATION_ERROR = 30
    RETRIABLE_ERROR = 40
    SAFETY_BLOCK = 50
    SUBMISSION_UNCERTAIN = 60
    DEPLOYMENT_ERROR = 70


SERVICE_LABEL = "com.wa.trader.paper1"
PROFILE_ID = "paper1"
STRATEGY_ID = "core_long"
STRATEGY_VERSION = "1.2.0"
SERVICE_INTERVAL_SECONDS = 3600


def service_label(profile_id: str) -> str:
    """Return the isolated launchd label for one validated profile."""

    return f"com.wa.trader.{profile_id}"

NORMAL_TERMINAL_STATUSES = frozenset(
    {
        "completed_dry_run",
        "completed_no_action",
        "completed_with_submissions",
        "completed_with_open_orders",
        "completed_with_partial_fills",
        "completed_with_rejections",
    }
)
