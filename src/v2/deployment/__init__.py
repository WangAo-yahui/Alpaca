"""提供 WA Trader v2 Stage H 的 macOS 本地部署能力。

作用：集中导出部署管理器与稳定退出码，供顶层 ``./wa`` 使用。
重要性：部署、运行和回滚必须与交易决策代码分离，避免运维操作绕过 Stage G 门禁。
"""

from v2.deployment.constants import ExitCode
from v2.deployment.manager import DeploymentManager

__all__ = ["DeploymentManager", "ExitCode"]
