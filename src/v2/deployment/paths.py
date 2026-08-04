"""构造 Stage H release、共享数据、锁、日志和 launchd 路径。

作用：把不可变 release 与 runtime、reports、market data、logs 明确分离。
重要性：回滚只能切换代码，绝不能删除账户状态、行情缓存、订单证据或日报。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from v2.deployment.constants import service_label


@dataclass(frozen=True)
class DeploymentPaths:
    profile_id: str
    environment: str
    service_label: str
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
    monitor_logs: Path
    locks: Path
    deploy_lock: Path
    run_lock: Path
    scheduler_lock: Path
    scheduler_state: Path
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
        profile_id: str = "live1",
        environment: str = "live",
    ) -> "DeploymentPaths":
        root = project_root.expanduser().resolve()
        resolved_home = (
            home.expanduser().resolve()
            if home is not None
            else Path.home().resolve()
        )
        var_root = root / "var"
        if environment not in {"paper", "live"}:
            raise ValueError("部署环境必须为paper或live")
        if not profile_id or any(
            character
            not in (
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789._-"
            )
            for character in profile_id
        ):
            raise ValueError("部署profile格式无效")
        deployment = (
            var_root / "deployment"
            if profile_id == "paper1"
            else var_root / "deployment" / profile_id
        )
        shared = var_root / "shared"
        locks = var_root / "locks"
        label = service_label(profile_id)
        launch_agents = (
            resolved_home / "Library" / "LaunchAgents"
        )
        return cls(
            profile_id=profile_id,
            environment=environment,
            service_label=label,
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
                / f"{environment}_submit_verified.json"
            ),
            shared=shared,
            runtime=shared / "runtime",
            reports=shared / "reports",
            market_data=shared / "market_data",
            logs=(
                shared / "logs"
                if profile_id == "paper1"
                else shared / "logs" / profile_id
            ),
            monitor_logs=(
                (
                    shared / "logs"
                    if profile_id == "paper1"
                    else shared / "logs" / profile_id
                )
                / "monitor"
            ),
            locks=locks,
            deploy_lock=(
                locks / "deploy.lock"
                if profile_id == "paper1"
                else locks / f"{profile_id}.deploy.lock"
            ),
            run_lock=locks / f"{profile_id}.run.lock",
            scheduler_lock=(
                locks / f"{profile_id}.scheduler.lock"
            ),
            scheduler_state=(
                shared
                / "runtime"
                / "scheduler"
                / f"{profile_id}.json"
            ),
            launch_agents=launch_agents,
            plist=launch_agents / f"{label}.plist",
            venv_python=root / ".Alpaca" / "bin" / "python",
            dotenv=(
                root / ".env_live"
                if environment == "live"
                else root / ".env"
            ),
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
            self.monitor_logs,
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
