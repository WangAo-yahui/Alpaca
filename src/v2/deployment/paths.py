"""构造 Stage H release、共享数据、锁、日志和 launchd 路径。

作用：把不可变 release 与 runtime、reports、market data、logs 明确分离。
重要性：回滚只能切换代码，绝不能删除账户状态、行情缓存、订单证据或日报。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from v2.deployment.constants import SERVICE_LABEL


@dataclass(frozen=True)
class DeploymentPaths:
    project_root: Path
    home: Path
    var_root: Path
    deployment_root: Path
    releases: Path
    staging: Path
    history: Path
    current: Path
    previous: Path
    verification_marker: Path
    shared: Path
    runtime: Path
    reports: Path
    market_data: Path
    logs: Path
    locks: Path
    deploy_lock: Path
    run_lock: Path
    launch_agents: Path
    plist: Path
    venv_python: Path
    dotenv: Path

    @classmethod
    def for_project(
        cls,
        project_root: Path,
        *,
        home: Path | None = None,
    ) -> "DeploymentPaths":
        root = project_root.expanduser().resolve()
        resolved_home = (
            home.expanduser().resolve()
            if home is not None
            else Path.home().resolve()
        )
        var_root = root / "var"
        deployment = var_root / "deployment"
        shared = var_root / "shared"
        locks = var_root / "locks"
        launch_agents = (
            resolved_home / "Library" / "LaunchAgents"
        )
        return cls(
            project_root=root,
            home=resolved_home,
            var_root=var_root,
            deployment_root=deployment,
            releases=deployment / "releases",
            staging=deployment / "staging",
            history=deployment / "history",
            current=deployment / "current.json",
            previous=deployment / "previous.json",
            verification_marker=(
                deployment
                / "paper_submit_verified.json"
            ),
            shared=shared,
            runtime=shared / "runtime",
            reports=shared / "reports",
            market_data=shared / "market_data",
            logs=shared / "logs",
            locks=locks,
            deploy_lock=locks / "deploy.lock",
            run_lock=locks / "paper1.run.lock",
            launch_agents=launch_agents,
            plist=launch_agents / f"{SERVICE_LABEL}.plist",
            venv_python=root / ".Alpaca" / "bin" / "python",
            dotenv=root / ".env",
        )

    def ensure_local_directories(self) -> None:
        for directory in (
            self.deployment_root,
            self.releases,
            self.staging,
            self.history,
            self.shared,
            self.runtime,
            self.reports,
            self.market_data,
            self.logs,
            self.locks,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def application_environment(
        self,
        *,
        git_commit: str,
        source_tree_hash: str = "unknown",
        source_tree_dirty: bool = False,
    ) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "WA_RUNTIME_ROOT": str(self.runtime),
                "WA_REPORTS_ROOT": str(self.reports),
                "WA_SHARED_DATA_ROOT": str(
                    self.market_data
                ),
                "WA_LOG_ROOT": str(self.logs),
                "WA_DOTENV_PATH": str(self.dotenv),
                "WA_RELEASE_GIT_COMMIT": git_commit,
                "WA_SOURCE_TREE_HASH": (
                    source_tree_hash
                ),
                "WA_SOURCE_TREE_DIRTY": (
                    "true"
                    if source_tree_dirty
                    else "false"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        return environment
