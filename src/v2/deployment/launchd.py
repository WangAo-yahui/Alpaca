"""生成并控制 WA Trader v2 按 profile 隔离的 launchd 用户服务。

作用：安装无凭据 plist，以固定间隔调用带防重入锁的 ``./wa _service-run``。
重要性：自动运行必须复用 current release 和 Stage G 门禁，plist 不得携带任何密钥或账户号。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from v2.deployment.constants import SERVICE_INTERVAL_SECONDS
from v2.deployment.paths import DeploymentPaths


CommandRunner = Callable[
    ..., subprocess.CompletedProcess[str]
]


def build_plist(
    paths: DeploymentPaths,
    *,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    if interval_seconds is None:
        interval_seconds = (
            60
            if paths.environment == "live"
            else SERVICE_INTERVAL_SECONDS
        )
    if interval_seconds < 60:
        raise ValueError("launchd运行间隔不得小于60秒")
    path_value = os.environ.get(
        "PATH",
        "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )
    return {
        "Label": paths.service_label,
        "ProgramArguments": [
            str(paths.project_root / "wa"),
            "_service-run",
            "--profile",
            paths.profile_id,
        ],
        "WorkingDirectory": str(paths.project_root),
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "ProcessType": "Background",
        "ThrottleInterval": 60,
        "StandardOutPath": str(
            paths.logs / "launchd.stdout.log"
        ),
        "StandardErrorPath": str(
            paths.logs / "launchd.stderr.log"
        ),
        "EnvironmentVariables": {
            "PATH": path_value,
            "PYTHONUNBUFFERED": "1",
            "WA_RUNTIME_ROOT": str(paths.runtime),
            "WA_REPORTS_ROOT": str(paths.reports),
            "WA_SHARED_DATA_ROOT": str(
                paths.market_data
            ),
            "WA_LOG_ROOT": str(paths.logs),
            "WA_DOTENV_PATH": str(paths.dotenv),
        },
    }


def write_plist(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(dict(payload), sort_keys=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


class LaunchdController:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        runner: CommandRunner = subprocess.run,
        uid: int | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.uid = os.getuid() if uid is None else uid

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.paths.service_label}"

    def _run(
        self,
        arguments: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            arguments,
            check=check,
            capture_output=True,
            text=True,
        )

    def install_and_start(self) -> None:
        write_plist(self.paths.plist, build_plist(self.paths))
        self._run(
            ["launchctl", "bootout", self.target],
            check=False,
        )
        self._run(
            [
                "launchctl",
                "bootstrap",
                self.domain,
                str(self.paths.plist),
            ],
            check=True,
        )
        self._run(
            ["launchctl", "kickstart", "-k", self.target],
            check=True,
        )

    def stop(self) -> None:
        self._run(
            ["launchctl", "bootout", self.target],
            check=False,
        )

    def start(self) -> None:
        self._run(
            [
                "launchctl",
                "bootstrap",
                self.domain,
                str(self.paths.plist),
            ],
            check=True,
        )
        self._run(
            ["launchctl", "kickstart", "-k", self.target],
            check=True,
        )

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> dict[str, Any]:
        result = self._run(
            ["launchctl", "print", self.target],
            check=False,
        )
        output = result.stdout or ""
        pid = None
        for line in output.splitlines():
            normalized = line.strip()
            if normalized.startswith("pid ="):
                try:
                    pid = int(normalized.split("=", 1)[1])
                except ValueError:
                    pid = None
        return {
            "label": self.paths.service_label,
            "loaded": result.returncode == 0,
            "running": pid is not None,
            "pid": pid,
        }
