"""编排 WA Trader v2 Stage H 的完整 macOS 本地运维闭环。

作用：实现 bootstrap、doctor、deploy、run、服务控制、状态、健康、日志和 rollback。
重要性：所有部署变更都必须经过锁、hash、dry-run 和健康检查；自动交易还需真实 submit 对账标记。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO
from zoneinfo import ZoneInfo

from v2.deployment.constants import (
    NORMAL_TERMINAL_STATUSES,
    SERVICE_INTERVAL_SECONDS,
    ExitCode,
)
from v2.deployment.launchd import LaunchdController
from v2.deployment.live_scheduler import (
    LiveScheduleSettings,
    MarketSession,
    ScheduleSlot,
    market_session_from_broker,
    next_schedule_slot,
    select_due_slot,
)
from v2.deployment.locks import (
    LockAlreadyHeldError,
    ProcessLock,
    inspect_process_lock,
)
from v2.deployment.paths import DeploymentPaths
from v2.deployment.redaction import (
    dotenv_secret_values,
    redact_text,
)
from v2.deployment.release import (
    ReleaseArtifact,
    ReleaseBuilder,
    atomic_write_json,
    load_json,
    manifests_contain_forbidden_text,
    source_tree_fingerprint,
)


class DeploymentError(RuntimeError):
    """A public, credential-free deployment failure."""


class DeploymentSafetyBlocked(DeploymentError):
    """A deploy or run request rejected by an explicit safety gate."""


CommandRunner = Callable[
    ..., subprocess.CompletedProcess[str]
]
NEW_YORK_TZ = ZoneInfo("America/New_York")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dotenv_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    result: set[str] = set()
    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name:
            result.add(name)
    return result


def _dotenv_selected_values(
    path: Path,
    names: tuple[str, ...],
) -> dict[str, str]:
    """Load only requested secret values without logging them."""

    selected = {
        name: os.environ.get(name, "").strip()
        for name in names
    }
    if path.is_file():
        for raw_line in path.read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if (
                not line
                or line.startswith("#")
                or "=" not in line
            ):
                continue
            name, raw_value = line.split("=", 1)
            name = name.strip()
            if name not in selected or selected[name]:
                continue
            value = raw_value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            selected[name] = value
    return selected


def classify_application_exit(
    returncode: int,
    cycle_state: Mapping[str, Any] | None,
    output: str = "",
) -> ExitCode:
    """Map the Stage G process and persisted cycle to Stage H codes."""

    status = str(
        (cycle_state or {}).get("status", "")
    ).lower()
    if status == "blocked_submission_uncertain":
        return ExitCode.SUBMISSION_UNCERTAIN
    if "uncertain" in status:
        return ExitCode.SUBMISSION_UNCERTAIN
    if returncode == 0 and status == "completed_no_action":
        return ExitCode.NO_ACTION
    if returncode == 0 and status in NORMAL_TERMINAL_STATUSES:
        return ExitCode.SUCCESS
    if returncode == 0 and not status:
        return ExitCode.SUCCESS
    if "retry" in status or "retri" in status:
        return ExitCode.RETRIABLE_ERROR
    if status.startswith("blocked"):
        return ExitCode.SAFETY_BLOCK
    normalized_output = output.upper()
    if "SUBMISSION_UNCERTAIN" in normalized_output:
        return ExitCode.SUBMISSION_UNCERTAIN
    if (
        "LIVE_TRADING_REJECTED" in normalized_output
        or "SAFETY" in normalized_output
        or "SUBMISSION_PREFLIGHT_BLOCKED"
        in normalized_output
    ):
        return ExitCode.SAFETY_BLOCK
    if (
        "BROKER_UNAVAILABLE" in normalized_output
        or "TEMPORARY_DATA" in normalized_output
        or "RETRYABLE" in normalized_output
        or "RUN_INTERRUPTED" in normalized_output
    ):
        return ExitCode.RETRIABLE_ERROR
    return ExitCode.CONFIGURATION_ERROR


class DeploymentManager:
    def __init__(
        self,
        project_root: Path,
        *,
        profile_id: str = "paper1",
        home: Path | None = None,
        runner: CommandRunner = subprocess.run,
        platform_name: str | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        root = project_root.expanduser().resolve()
        profile_path = (
            root
            / "config"
            / "v2"
            / "profiles"
            / f"{profile_id}.json"
        )
        try:
            source_profile = load_json(profile_path)
        except Exception as error:
            if profile_id != "paper1":
                raise DeploymentError(
                    f"无法加载部署profile：{profile_id}"
                ) from error
            source_profile = {
                "profile_id": "paper1",
                "environment": "paper",
                "enabled": True,
                "credential_key_env": "ALPACA_API_KEY",
                "credential_secret_env": "ALPACA_SECRET_KEY",
                "strategy": {
                    "strategy_id": "core_long",
                    "strategy_version": "1.2.0",
                },
                "submission_policy": (
                    "alpaca_paper@1.0.0"
                ),
            }
        source_profile.setdefault(
            "credential_key_env",
            (
                "ALPACA_API_KEY"
                if profile_id == "paper1"
                else ""
            ),
        )
        source_profile.setdefault(
            "credential_secret_env",
            (
                "ALPACA_SECRET_KEY"
                if profile_id == "paper1"
                else ""
            ),
        )
        source_profile.setdefault(
            "strategy",
            {
                "strategy_id": "core_long",
                "strategy_version": "1.2.0",
            },
        )
        environment = str(
            source_profile.get("environment", "")
        )
        if environment not in {"paper", "live"}:
            raise DeploymentError("部署profile环境无效")
        strategy = source_profile.get("strategy", {})
        if not isinstance(strategy, Mapping):
            raise DeploymentError("部署profile策略无效")
        self.profile_id = profile_id
        self.profile_environment = environment
        self.profile = source_profile
        self.strategy_id = str(
            strategy.get("strategy_id", "")
        )
        self.strategy_version = str(
            strategy.get("strategy_version", "")
        )
        self.paths = DeploymentPaths.for_project(
            root,
            home=home,
            profile_id=profile_id,
            environment=environment,
        )
        self.runner = runner
        self.platform_name = (
            platform_name or platform.system()
        )
        self.stdout = stdout or sys.stdout
        self.release_builder = ReleaseBuilder(self.paths)
        self.launchd = LaunchdController(
            self.paths,
            runner=runner,
        )

    def _print(self, message: str) -> None:
        print(
            message,
            file=self.stdout,
            flush=True,
        )

    def _run(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            arguments,
            cwd=str(cwd or self.paths.project_root),
            env=(
                dict(environment)
                if environment is not None
                else None
            ),
            check=check,
            capture_output=True,
            text=True,
        )

    def _git_commit(self) -> str:
        result = self._run(
            ["git", "rev-parse", "HEAD"]
        )
        value = result.stdout.strip().lower()
        if (
            len(value) != 40
            or any(
                character not in "0123456789abcdef"
                for character in value
            )
        ):
            raise DeploymentError("Git commit格式无效")
        return value

    def _git_clean(self) -> bool:
        result = self._run(
            ["git", "status", "--porcelain"],
            check=True,
        )
        return not result.stdout.strip()

    def _credential_names_present(self) -> bool:
        names = _dotenv_names(self.paths.dotenv)
        available = names | set(os.environ)
        key_present = (
            str(
                self.profile.get(
                    "credential_key_env", ""
                )
            )
            in available
        )
        secret_present = (
            str(
                self.profile.get(
                    "credential_secret_env", ""
                )
            )
            in available
        )
        return key_present and secret_present

    def _binding_path(self) -> Path:
        shared = (
            self.paths.runtime
            / "account_bindings"
            / f"{self.profile_id}.json"
        )
        source = (
            self.paths.project_root
            / "account_bindings"
            / f"{self.profile_id}.json"
        )
        return shared if shared.is_file() else source

    def doctor(self) -> dict[str, Any]:
        profile_path = (
            self.paths.project_root
            / "config"
            / "v2"
            / "profiles"
            / f"{self.profile_id}.json"
        )
        submission_reference = str(
            self.profile.get(
                "submission_policy", ""
            )
        )
        policy_id, separator, policy_version = (
            submission_reference.partition("@")
        )
        submission_path = (
            self.paths.project_root
            / "config"
            / "v2"
            / "submission_policies"
            / (
                f"{policy_id}-{policy_version}.json"
                if separator
                else "__invalid__.json"
            )
        )
        profile: dict[str, Any] = {}
        submission: dict[str, Any] = {}
        try:
            profile = load_json(profile_path)
        except Exception:
            pass
        try:
            submission = load_json(submission_path)
        except Exception:
            pass
        binding = self._binding_path()
        binding_valid = False
        if binding.is_file():
            try:
                payload = load_json(binding)
                digest = str(
                    payload.get("account_id_hash", "")
                )
                binding_valid = (
                    payload.get("profile_id")
                    == self.profile_id
                    and len(digest) == 64
                )
            except Exception:
                binding_valid = False
        codex_available = (
            shutil.which("codex") is not None
        )
        checks = [
            {
                "name": "macos",
                "ok": self.platform_name == "Darwin",
                "detail": self.platform_name,
            },
            {
                "name": "python_venv",
                "ok": self.paths.venv_python.is_file(),
                "detail": str(self.paths.venv_python),
            },
            {
                "name": "codex",
                "ok": codex_available,
                "detail": "available" if codex_available else "missing",
            },
            {
                "name": f"{self.profile_id}_profile",
                "ok": (
                    profile.get("profile_id")
                    == self.profile_id
                    and profile.get("environment")
                    == self.profile_environment
                    and profile.get("enabled") is True
                    and profile.get("submission_policy")
                    == submission_reference
                ),
                "detail": self.profile_environment,
            },
            {
                "name": "credentials",
                "ok": self._credential_names_present(),
                "detail": "present" if self._credential_names_present() else "missing",
            },
            {
                "name": "account_binding",
                "ok": binding_valid,
                "detail": str(binding),
            },
            {
                "name": "submission_policy",
                "ok": (
                    submission.get("environment")
                    == self.profile_environment
                    and submission.get("allow_submit") is True
                    and submission.get("allow_direct_replace") is False
                    and isinstance(
                        submission.get("deployment_switches"),
                        dict,
                    )
                    and (
                        submission["deployment_switches"].get(
                            f"{self.profile_environment}_submission_enabled"
                        )
                        is True
                        or (
                            self.profile_environment
                            == "paper"
                            and "paper_submission_enabled"
                            not in submission[
                                "deployment_switches"
                            ]
                            and submission[
                                "deployment_switches"
                            ].get(
                                "live_submission_enabled"
                            )
                            is False
                        )
                    )
                ),
                "detail": (
                    f"{self.profile_environment} write gate"
                ),
            },
            {
                "name": "environment_isolated",
                "ok": (
                    profile.get("environment")
                    == submission.get("environment")
                    == self.profile_environment
                ),
                "detail": self.paths.dotenv.name,
            },
        ]
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "environment": self.profile_environment,
            "healthy": all(
                bool(item["ok"]) for item in checks
            ),
            "checks": checks,
        }

    def _ensure_venv(self) -> None:
        if not self.paths.venv_python.is_file():
            self._run(
                [
                    sys.executable,
                    "-m",
                    "venv",
                    str(
                        self.paths.project_root
                        / ".Alpaca"
                    ),
                ]
            )
        self._run(
            [
                str(self.paths.venv_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(
                    self.paths.project_root
                    / "requirements.lock"
                ),
            ]
        )

    def _copy_account_binding(self) -> None:
        source = (
            self.paths.project_root
            / "account_bindings"
            / f"{self.profile_id}.json"
        )
        target = (
            self.paths.runtime
            / "account_bindings"
            / f"{self.profile_id}.json"
        )
        if not source.is_file() and not target.is_file():
            raise DeploymentError(
                f"{self.profile_id}账户绑定不存在"
            )
        if target.is_file():
            if source.is_file():
                source_payload = load_json(source)
                target_payload = load_json(target)
                if (
                    source_payload.get("account_id_hash")
                    != target_payload.get(
                        "account_id_hash"
                    )
                ):
                    raise DeploymentSafetyBlocked(
                        "共享账户绑定与当前profile不一致"
                    )
            return
        target.parent.mkdir(
            parents=True, exist_ok=True
        )
        shutil.copy2(source, target)
        target.chmod(0o600)

    def _seed_shared_market_data(self) -> None:
        source_daily = (
            self.paths.project_root
            / "data"
            / "bars"
            / "daily"
        )
        target_daily = (
            self.paths.market_data
            / "market"
            / "daily"
        )
        target_daily.mkdir(
            parents=True, exist_ok=True
        )
        if source_daily.is_dir():
            for source in source_daily.glob("*.json"):
                target = target_daily / source.name
                if not target.exists():
                    shutil.copy2(source, target)
        source_assets = (
            self.paths.project_root
            / "data"
            / "snapshots"
            / "assets.json"
        )
        target_assets = (
            self.paths.market_data
            / "assets"
            / "assets.json"
        )
        if source_assets.is_file() and not target_assets.exists():
            target_assets.parent.mkdir(
                parents=True, exist_ok=True
            )
            shutil.copy2(source_assets, target_assets)

    def run_tests(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        environment["PYTHONPYCACHEPREFIX"] = (
            "/private/tmp/wa_stage_h_pycache"
        )
        self._run(
            [
                str(self.paths.venv_python),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/v2",
                "-p",
                "test_*.py",
            ],
            environment=environment,
        )

    def compile_check(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        environment["PYTHONPYCACHEPREFIX"] = (
            "/private/tmp/wa_stage_h_pycache"
        )
        self._run(
            [
                str(self.paths.venv_python),
                "-m",
                "compileall",
                "-q",
                "src/v2",
                "tests/v2",
            ],
            environment=environment,
        )

    def static_write_scan(self) -> None:
        source_root = self.paths.project_root / "src" / "v2"
        submit_files: list[str] = []
        cancel_files: list[str] = []
        forbidden: list[str] = []
        v1_imports: list[str] = []
        call_present = lambda method, text: bool(
            re.search(
                r"\.\s*"
                + re.escape(method)
                + r"\s*\(",
                text,
            )
        )
        for path in source_root.rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            relative = path.relative_to(
                self.paths.project_root
            ).as_posix()
            if call_present("submit_order", content):
                submit_files.append(relative)
            if call_present(
                "cancel_order_by_id", content
            ):
                cancel_files.append(relative)
            if any(
                call_present(method, content)
                for method in (
                    "replace_order_by_id",
                    "cancel_orders",
                    "close_position",
                    "close_all_positions",
                )
            ):
                forbidden.append(relative)
            legacy_module = "v" + "1"
            if (
                "from " + legacy_module in content
                or "import " + legacy_module in content
            ):
                v1_imports.append(relative)
        if submit_files != [
            "src/v2/trading/order_submitter.py"
        ]:
            raise DeploymentSafetyBlocked(
                "submit_order写白名单扫描失败"
            )
        if cancel_files != [
            "src/v2/trading/order_action_executor.py"
        ]:
            raise DeploymentSafetyBlocked(
                "cancel写白名单扫描失败"
            )
        if forbidden or v1_imports:
            raise DeploymentSafetyBlocked(
                "发现禁止broker写API或v1导入"
            )

    def bootstrap(self) -> dict[str, Any]:
        if self.platform_name != "Darwin":
            raise DeploymentError(
                "Stage H只支持macOS"
            )
        self.paths.ensure_local_directories()
        self._ensure_venv()
        self._copy_account_binding()
        self._seed_shared_market_data()
        report = self.doctor()
        if not report["healthy"]:
            raise DeploymentError("doctor检查未通过")
        self.run_tests()
        self.compile_check()
        self.static_write_scan()
        commit = self._git_commit()
        result = self._run_application(
            application_root=self.paths.project_root,
            git_commit=commit,
            allow_trade=False,
            command_name="bootstrap-dry-run",
        )
        if result not in {
            ExitCode.SUCCESS,
            ExitCode.NO_ACTION,
        }:
            raise DeploymentError(
                f"bootstrap真实dry-run失败：{int(result)}"
            )
        return {
            **report,
            "dry_run_exit_code": int(result),
            "directories_ready": True,
        }

    def _current_document(
        self,
    ) -> dict[str, Any] | None:
        return (
            load_json(self.paths.current)
            if self.paths.current.is_file()
            else None
        )

    def _previous_document(
        self,
    ) -> dict[str, Any] | None:
        return (
            load_json(self.paths.previous)
            if self.paths.previous.is_file()
            else None
        )

    def _release_from_document(
        self,
        document: Mapping[str, Any],
    ) -> ReleaseArtifact:
        root = Path(str(document["release_path"]))
        artifact = self.release_builder.validate(root)
        if (
            artifact.release_id
            != document.get("release_id")
            or artifact.manifest_hash
            != document.get("manifest_hash")
        ):
            raise DeploymentError(
                "部署指针与release manifest不一致"
            )
        return artifact

    def _deployment_document(
        self,
        artifact: ReleaseArtifact,
        *,
        trading_enabled: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "environment": self.profile_environment,
            "release_id": artifact.release_id,
            "release_path": str(artifact.root),
            "manifest_hash": artifact.manifest_hash,
            "git_commit": artifact.git_commit,
            "trading_enabled": trading_enabled,
            "installed_at": _utc_now(),
        }

    def _save_history(
        self,
        action: str,
        payload: Mapping[str, Any],
    ) -> None:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_write_json(
            self.paths.history
            / f"{timestamp}-{action}.json",
            {
                "schema_version": "1.0",
                "action": action,
                "recorded_at": _utc_now(),
                **dict(payload),
            },
        )

    def _switch_current(
        self,
        document: Mapping[str, Any],
    ) -> None:
        current = self._current_document()
        if current is not None:
            atomic_write_json(
                self.paths.previous,
                current,
            )
        atomic_write_json(
            self.paths.current,
            dict(document),
        )

    def _restore_pointers(
        self,
        current: Mapping[str, Any] | None,
        previous: Mapping[str, Any] | None,
    ) -> None:
        if current is None:
            self.paths.current.unlink(missing_ok=True)
        else:
            atomic_write_json(
                self.paths.current, dict(current)
            )
        if previous is None:
            self.paths.previous.unlink(missing_ok=True)
        else:
            atomic_write_json(
                self.paths.previous, dict(previous)
            )

    def _trading_deploy_verified(self) -> bool:
        if not self.paths.verification_marker.is_file():
            return False
        try:
            payload = load_json(
                self.paths.verification_marker
            )
        except Exception:
            return False
        return (
            payload.get("profile_id") == self.profile_id
            and int(payload.get("submitted_count", 0)) > 0
            and int(payload.get("uncertain_count", 1)) == 0
            and payload.get("reconciled") is True
        )

    def deploy(
        self,
        *,
        enable_trading: bool,
    ) -> dict[str, Any]:
        try:
            lock = ProcessLock(
                self.paths.deploy_lock,
                "deploy",
            )
            lock.acquire()
        except LockAlreadyHeldError as error:
            raise DeploymentError(str(error)) from error
        old_current = self._current_document()
        old_previous = self._previous_document()
        switched = False
        try:
            if (
                enable_trading
                and self.profile_environment == "paper"
                and not self._trading_deploy_verified()
            ):
                raise DeploymentSafetyBlocked(
                    "尚无自然真实submit并成功对账的验证记录；"
                    "禁止自动交易部署"
                )
            report = self.doctor()
            if not report["healthy"]:
                raise DeploymentError("doctor检查未通过")
            if not self._git_clean():
                raise DeploymentError(
                    "工作树不干净，拒绝构建release"
                )
            commit = self._git_commit()
            artifact = self.release_builder.build_staging(
                git_commit=commit
            )
            self.run_tests()
            self.compile_check()
            self.static_write_scan()
            secret_values = dotenv_secret_values(
                self.paths.dotenv
            )
            if manifests_contain_forbidden_text(
                [artifact.root],
                secret_values,
            ):
                raise DeploymentSafetyBlocked(
                    "release包含凭据值"
                )
            dry_result = self._run_application(
                application_root=artifact.root,
                git_commit=commit,
                allow_trade=False,
                command_name="deploy-dry-run",
            )
            if dry_result not in {
                ExitCode.SUCCESS,
                ExitCode.NO_ACTION,
            }:
                raise DeploymentError(
                    f"release真实dry-run失败：{int(dry_result)}"
                )
            installed = self.release_builder.install(
                artifact
            )
            document = self._deployment_document(
                installed,
                trading_enabled=enable_trading,
            )
            self._switch_current(document)
            switched = True
            self.launchd.install_and_start()
            health = self.health()
            if health["status"] not in {
                "healthy",
                "degraded",
            }:
                raise DeploymentError(
                    "部署后health检查失败"
                )
            self._save_history(
                "deploy",
                {
                    "current": document,
                    "health": health,
                },
            )
            return {
                "deployed": True,
                "current": document,
                "health": health,
                "dry_run_exit_code": int(dry_result),
            }
        except Exception:
            if switched:
                self.launchd.stop()
                self._restore_pointers(
                    old_current, old_previous
                )
                if old_current is not None:
                    self.launchd.install_and_start()
            self._save_history(
                "deploy-failed",
                {
                    "restored_current": old_current,
                },
            )
            raise
        finally:
            lock.release()

    def _latest_cycle_state(
        self,
    ) -> tuple[Path, dict[str, Any]] | None:
        pattern = (
            f"accounts/{self.profile_id}/strategies/"
            f"{self.strategy_id}/{self.strategy_version}/"
            "*/cycles/*/cycle_state.json"
        )
        candidates = sorted(
            self.paths.runtime.glob(pattern)
        )
        for path in reversed(candidates):
            try:
                return path, load_json(path)
            except Exception:
                continue
        return None

    def _run_application(
        self,
        *,
        application_root: Path,
        git_commit: str,
        allow_trade: bool,
        command_name: str,
        force_full: bool = False,
        bind_account: bool = False,
        maintenance_only: bool = False,
    ) -> ExitCode:
        before = self._latest_cycle_state()
        before_path = before[0] if before else None
        try:
            lock = ProcessLock(
                self.paths.run_lock,
                command_name,
            )
            lock.acquire()
        except LockAlreadyHeldError:
            return ExitCode.ALREADY_RUNNING
        self.paths.logs.mkdir(
            parents=True, exist_ok=True
        )
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")
        log_path = (
            self.paths.logs
            / f"{timestamp}-{command_name}.log"
        )
        source_run = (
            application_root.resolve()
            == self.paths.project_root.resolve()
        )
        environment = self.paths.application_environment(
            git_commit=git_commit,
            source_tree_hash=(
                source_tree_fingerprint(
                    application_root
                )
            ),
            source_tree_dirty=(
                source_run
                and not self._git_clean()
            ),
        )
        command = [
            str(self.paths.venv_python),
            "-u",
            str(application_root / "src" / "v2" / "main.py"),
            "--profile",
            self.profile_id,
            "--unattended",
        ]
        if bind_account:
            command.append("--bind-account")
        if allow_trade:
            command.append("--allow-trade")
        if force_full:
            command.append("--force-full")
        if maintenance_only:
            command.extend(
                [
                    "--new-cycle",
                    "--maintenance-only",
                ]
            )
        secrets = dotenv_secret_values(
            self.paths.dotenv
        )
        captured: list[str] = []
        process: subprocess.Popen[str] | None = None
        terminated = False

        def terminate(
            signum: int,
            frame: object,
        ) -> None:
            del signum, frame
            nonlocal terminated
            terminated = True
            if process is not None and process.poll() is None:
                process.terminate()

        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(application_root),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with log_path.open(
                "w", encoding="utf-8"
            ) as log:
                assert process.stdout is not None
                for line in process.stdout:
                    safe = redact_text(
                        line,
                        secret_values=secrets,
                    )
                    captured.append(safe)
                    self._print(safe.rstrip("\n"))
                    log.write(safe)
                    log.flush()
                try:
                    returncode = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    returncode = process.wait()
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
            lock.release()
        latest = self._latest_cycle_state()
        if (
            latest is not None
            and latest[0] == before_path
            and returncode != 0
        ):
            latest = None
        cycle_state = latest[1] if latest else None
        result = classify_application_exit(
            returncode,
            cycle_state,
            "".join(captured),
        )
        if (
            terminated
            and result
            not in {
                ExitCode.RETRIABLE_ERROR,
                ExitCode.SAFETY_BLOCK,
            }
        ):
            return ExitCode.DEPLOYMENT_ERROR
        if allow_trade and latest is not None:
            self._record_submit_verification(
                latest[0], latest[1]
            )
        return result

    def _record_submit_verification(
        self,
        cycle_state_path: Path,
        state: Mapping[str, Any],
    ) -> None:
        cycle_root = cycle_state_path.parent
        broker_path = (
            cycle_root
            / "orders"
            / "broker_submission.json"
        )
        reconciliation_path = (
            cycle_root
            / "orders"
            / "reconciliation.json"
        )
        if (
            not broker_path.is_file()
            or not reconciliation_path.is_file()
        ):
            return
        broker = load_json(broker_path)
        reconciliation = load_json(
            reconciliation_path
        )
        submitted = int(
            broker.get("submitted_count", 0)
        )
        uncertain = int(
            broker.get("uncertain_count", 0)
        )
        if (
            submitted <= 0
            or uncertain != 0
            or state.get("current_step") != "COMPLETE"
            or reconciliation.get("errors")
        ):
            return
        atomic_write_json(
            self.paths.verification_marker,
            {
                "schema_version": "1.0",
                "profile_id": self.profile_id,
                "environment": self.profile_environment,
                "cycle_id": state.get("cycle_id"),
                "submitted_count": submitted,
                "uncertain_count": uncertain,
                "reconciled": True,
                "verified_at": _utc_now(),
            },
        )

    def run(
        self,
        *,
        allow_trade: bool,
        force_full: bool = False,
        bind_account: bool = False,
        maintenance_only: bool = False,
    ) -> ExitCode:
        root = self.paths.project_root
        commit = self._git_commit()
        self._print(
            "手工运行使用当前工作区源码；"
            "launchd仍使用已部署release"
        )
        return self._run_application(
            application_root=root,
            git_commit=commit,
            allow_trade=allow_trade,
            force_full=(
                allow_trade or force_full
                if self.profile_environment == "paper"
                else force_full
            ),
            **(
                {"bind_account": True}
                if bind_account
                else {}
            ),
            **(
                {"maintenance_only": True}
                if maintenance_only
                else {}
            ),
            command_name=(
                "manual-paper"
                if (
                    allow_trade
                    and self.profile_environment == "paper"
                )
                else f"manual-{self.profile_environment}-trade"
                if allow_trade
                else f"manual-{self.profile_environment}-dry-run"
            ),
        )

    def service_run(self) -> ExitCode:
        current = self._current_document()
        if current is None:
            return ExitCode.CONFIGURATION_ERROR
        artifact = self._release_from_document(current)
        if self.profile_environment == "live":
            return self._scheduled_live_service_run(
                current=current,
                artifact=artifact,
            )
        return self._run_application(
            application_root=artifact.root,
            git_commit=artifact.git_commit,
            allow_trade=bool(
                current.get("trading_enabled")
            ),
            command_name=f"launchd-{self.profile_id}",
        )

    def _schedule_settings(
        self,
    ) -> LiveScheduleSettings:
        schedule = self.profile.get("schedule")
        return LiveScheduleSettings.from_mapping(
            (
                schedule
                if isinstance(schedule, Mapping)
                else None
            )
        )

    def _default_scheduler_state(
        self,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "timezone": "America/New_York",
            "updated_at": _utc_now(),
            "calendar_fetched_for": None,
            "calendar_fetched_at": None,
            "calendar_sessions": [],
            "slots": {},
            "last_slot_id": None,
            "last_slot_kind": None,
            "last_status": "not_started",
            "last_exit_code": None,
            "last_cycle_id": None,
            "service_status": "not_started",
            "close_check_status": "not_started",
            "calendar_error": None,
            "calendar_failure_count": 0,
            "calendar_retry_not_before": None,
        }

    def _load_scheduler_state(
        self,
    ) -> dict[str, Any]:
        if not self.paths.scheduler_state.is_file():
            return self._default_scheduler_state()
        try:
            payload = load_json(
                self.paths.scheduler_state
            )
        except Exception as error:
            state = self._default_scheduler_state()
            state["last_status"] = "state_invalid"
            state["calendar_error"] = (
                error.__class__.__name__
            )
            return state
        slots = payload.get("slots")
        if not isinstance(slots, dict):
            payload["slots"] = {}
        sessions = payload.get("calendar_sessions")
        if not isinstance(sessions, list):
            payload["calendar_sessions"] = []
        return payload

    def _save_scheduler_state(
        self,
        state: Mapping[str, Any],
    ) -> None:
        payload = dict(state)
        payload["updated_at"] = _utc_now()
        slots = payload.get("slots", {})
        if isinstance(slots, dict) and len(slots) > 200:
            payload["slots"] = dict(
                sorted(slots.items())[-200:]
            )
        atomic_write_json(
            self.paths.scheduler_state,
            payload,
        )

    def _monitor_event(
        self,
        *,
        event: str,
        details: Mapping[str, Any],
        now: datetime | None = None,
    ) -> None:
        current = (
            now.astimezone(NEW_YORK_TZ)
            if now is not None
            else datetime.now(NEW_YORK_TZ)
        )
        self.paths.monitor_logs.mkdir(
            parents=True, exist_ok=True
        )
        path = (
            self.paths.monitor_logs
            / f"{current.date().isoformat()}.jsonl"
        )
        payload = {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "event": event,
            "recorded_at": _utc_now(),
            "new_york_time": current.isoformat(),
            **dict(details),
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            file.flush()
            os.fsync(file.fileno())

    def _cached_market_sessions(
        self,
        state: Mapping[str, Any],
    ) -> list[MarketSession]:
        sessions: list[MarketSession] = []
        for payload in state.get(
            "calendar_sessions", []
        ):
            if not isinstance(payload, Mapping):
                continue
            try:
                sessions.append(
                    MarketSession.from_mapping(payload)
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sessions

    def _fetch_market_sessions(
        self,
        *,
        start: date,
        end: date,
    ) -> list[MarketSession]:
        key_name = str(
            self.profile.get(
                "credential_key_env", ""
            )
        )
        secret_name = str(
            self.profile.get(
                "credential_secret_env", ""
            )
        )
        values = _dotenv_selected_values(
            self.paths.dotenv,
            (key_name, secret_name),
        )
        if (
            not values.get(key_name)
            or not values.get(secret_name)
        ):
            raise DeploymentError(
                "Live交易日历缺少profile凭据"
            )
        query = urllib.parse.urlencode(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
        request = urllib.request.Request(
            (
                "https://api.alpaca.markets/"
                f"v2/calendar?{query}"
            ),
            headers={
                "APCA-API-KEY-ID": values[key_name],
                "APCA-API-SECRET-KEY": (
                    values[secret_name]
                ),
                "Accept": "application/json",
                "User-Agent": "WA-Trader-v2-scheduler",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30
            ) as response:
                payload = json.loads(
                    response.read().decode("utf-8")
                )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise DeploymentError(
                "Alpaca交易日历暂时不可用"
            ) from error
        if not isinstance(payload, list):
            raise DeploymentError(
                "Alpaca交易日历响应无效"
            )
        sessions = [
            market_session_from_broker(value)
            for value in payload
        ]
        return sorted(
            sessions,
            key=lambda item: item.open_at,
        )

    def _market_sessions(
        self,
        *,
        now: datetime,
        state: dict[str, Any],
    ) -> list[MarketSession] | None:
        current_date = now.astimezone(
            NEW_YORK_TZ
        ).date()
        if (
            state.get("calendar_fetched_for")
            == current_date.isoformat()
        ):
            sessions = self._cached_market_sessions(
                state
            )
            if sessions:
                return sessions
        retry_value = state.get(
            "calendar_retry_not_before"
        )
        if retry_value:
            try:
                retry_at = datetime.fromisoformat(
                    str(retry_value).replace(
                        "Z", "+00:00"
                    )
                )
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(
                        tzinfo=timezone.utc
                    )
                if datetime.now(timezone.utc) < (
                    retry_at.astimezone(timezone.utc)
                ):
                    return None
            except ValueError:
                pass
        try:
            sessions = self._fetch_market_sessions(
                start=current_date,
                end=current_date + timedelta(days=10),
            )
        except Exception as error:
            failure_count = int(
                state.get(
                    "calendar_failure_count", 0
                )
                or 0
            ) + 1
            delay_minutes = min(
                15, 2 ** min(failure_count - 1, 4)
            )
            state["calendar_error"] = (
                error.__class__.__name__
            )
            state["calendar_failure_count"] = (
                failure_count
            )
            state["calendar_retry_not_before"] = (
                datetime.now(timezone.utc)
                + timedelta(minutes=delay_minutes)
            ).isoformat()
            state["service_status"] = (
                "calendar_failed_retriable"
            )
            self._save_scheduler_state(state)
            self._monitor_event(
                event="calendar_failed",
                details={
                    "error_type": (
                        error.__class__.__name__
                    ),
                    "retry_after_minutes": (
                        delay_minutes
                    ),
                },
                now=now,
            )
            return None
        state["calendar_fetched_for"] = (
            current_date.isoformat()
        )
        state["calendar_fetched_at"] = _utc_now()
        state["calendar_sessions"] = [
            session.to_dict()
            for session in sessions
        ]
        state["calendar_error"] = None
        state["calendar_failure_count"] = 0
        state["calendar_retry_not_before"] = None
        self._save_scheduler_state(state)
        return sessions

    def _claim_schedule_slot(
        self,
        *,
        state: dict[str, Any],
        slot: ScheduleSlot,
        now: datetime,
    ) -> None:
        slots = state.setdefault("slots", {})
        existing = slots.get(slot.slot_id, {})
        attempts = (
            int(existing.get("attempts", 0) or 0)
            if isinstance(existing, Mapping)
            else 0
        )
        record = {
            **slot.to_dict(),
            "status": "running",
            "attempts": attempts + 1,
            "claimed_at": now.astimezone(
                NEW_YORK_TZ
            ).isoformat(),
            "completed_at": None,
            "exit_code": None,
            "cycle_id": None,
        }
        slots[slot.slot_id] = record
        state["last_slot_id"] = slot.slot_id
        state["last_slot_kind"] = slot.kind
        state["last_status"] = "running"
        state["service_status"] = "running"
        if slot.kind == "close":
            state["close_check_status"] = "running"
        self._save_scheduler_state(state)
        self._monitor_event(
            event="slot_claimed",
            details=record,
            now=now,
        )

    def _finish_schedule_slot(
        self,
        *,
        state: dict[str, Any],
        slot: ScheduleSlot,
        result: ExitCode,
        now: datetime,
        cycle_id: str | None,
    ) -> str:
        statuses = {
            ExitCode.SUCCESS: "completed",
            ExitCode.NO_ACTION: "completed",
            ExitCode.ALREADY_RUNNING: "waiting",
            ExitCode.RETRIABLE_ERROR: (
                "failed_retriable"
            ),
            ExitCode.SUBMISSION_UNCERTAIN: (
                "blocked_uncertain"
            ),
            ExitCode.SAFETY_BLOCK: "failed_closed",
            ExitCode.CONFIGURATION_ERROR: "failed_closed",
            ExitCode.DEPLOYMENT_ERROR: "failed_closed",
        }
        status = statuses.get(
            result, "failed_closed"
        )
        slots = state.setdefault("slots", {})
        existing = slots.get(slot.slot_id, {})
        record = (
            dict(existing)
            if isinstance(existing, Mapping)
            else slot.to_dict()
        )
        if status == "waiting":
            record["attempts"] = max(
                0,
                int(record.get("attempts", 1)) - 1,
            )
        record.update(
            {
                "status": status,
                "completed_at": now.astimezone(
                    NEW_YORK_TZ
                ).isoformat(),
                "exit_code": int(result),
                "cycle_id": cycle_id,
            }
        )
        slots[slot.slot_id] = record
        state["last_status"] = status
        state["service_status"] = "idle"
        state["last_exit_code"] = int(result)
        state["last_cycle_id"] = cycle_id
        if slot.kind == "close":
            state["close_check_status"] = status
        self._save_scheduler_state(state)
        self._monitor_event(
            event="slot_finished",
            details=record,
            now=now,
        )
        return status

    def _screen_off(self) -> bool:
        try:
            result = self._run(
                ["/usr/bin/pmset", "displaysleepnow"],
                check=False,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _close_check_can_sleep(
        self,
        *,
        result: ExitCode,
        latest: tuple[Path, dict[str, Any]] | None,
    ) -> bool:
        if result not in {
            ExitCode.SUCCESS,
            ExitCode.NO_ACTION,
        }:
            return False
        if latest is None:
            return False
        state_path, state = latest
        if state.get("status") not in NORMAL_TERMINAL_STATUSES:
            return False
        broker, reconciliation = (
            self._latest_submission(state_path)
        )
        return (
            int(broker.get("uncertain_count", 0))
            == 0
            and not reconciliation.get("errors")
            and not inspect_process_lock(
                self.paths.run_lock
            )["alive"]
        )

    def _scheduled_live_service_run(
        self,
        *,
        current: Mapping[str, Any],
        artifact: ReleaseArtifact,
    ) -> ExitCode:
        settings = self._schedule_settings()
        now = datetime.now(NEW_YORK_TZ)
        try:
            scheduler_lock = ProcessLock(
                self.paths.scheduler_lock,
                f"schedule-{self.profile_id}",
            )
            scheduler_lock.acquire()
        except LockAlreadyHeldError:
            return ExitCode.ALREADY_RUNNING
        try:
            state = self._load_scheduler_state()
            if state.get("last_status") == "state_invalid":
                self._monitor_event(
                    event="scheduler_state_invalid",
                    details={},
                    now=now,
                )
                return ExitCode.DEPLOYMENT_ERROR
            sessions = self._market_sessions(
                now=now,
                state=state,
            )
            if sessions is None:
                return ExitCode.RETRIABLE_ERROR
            slot_records = state.get("slots", {})
            if not isinstance(slot_records, Mapping):
                slot_records = {}
            slot = select_due_slot(
                sessions,
                now=now,
                slot_records=slot_records,
                settings=settings,
            )
            if slot is None:
                state["service_status"] = "idle"
                self._save_scheduler_state(state)
                return ExitCode.NO_ACTION
            self._claim_schedule_slot(
                state=state,
                slot=slot,
                now=now,
            )
            result = self._run_application(
                application_root=artifact.root,
                git_commit=artifact.git_commit,
                allow_trade=(
                    bool(current.get("trading_enabled"))
                    if slot.kind == "intraday"
                    else False
                ),
                maintenance_only=(
                    slot.kind == "close"
                ),
                command_name=(
                    f"launchd-{self.profile_id}-"
                    f"{slot.kind}"
                ),
            )
            latest = self._latest_cycle_state()
            cycle_id = (
                str(latest[1].get("cycle_id"))
                if latest is not None
                and latest[1].get("cycle_id")
                else None
            )
            self._finish_schedule_slot(
                state=state,
                slot=slot,
                result=result,
                now=datetime.now(NEW_YORK_TZ),
                cycle_id=cycle_id,
            )
            if (
                slot.kind == "close"
                and settings.display_sleep_after_close
                and self._close_check_can_sleep(
                    result=result,
                    latest=latest,
                )
            ):
                slept = self._screen_off()
                state["close_check_status"] = (
                    "completed_display_sleep"
                    if slept
                    else "display_sleep_failed"
                )
                self._save_scheduler_state(state)
                self._monitor_event(
                    event="display_sleep",
                    details={"success": slept},
                    now=datetime.now(NEW_YORK_TZ),
                )
            return result
        finally:
            scheduler_lock.release()

    def start(self) -> None:
        current = self._current_document()
        if current is None:
            raise DeploymentError("尚未部署current release")
        self._release_from_document(current)
        self.launchd.install_and_start()

    def stop(self) -> None:
        self.launchd.stop()

    def restart(self) -> None:
        current = self._current_document()
        if current is None:
            raise DeploymentError("尚未部署current release")
        self.launchd.install_and_start()

    def _latest_submission(
        self,
        state_path: Path | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if state_path is None:
            return {}, {}
        orders = state_path.parent / "orders"
        broker = (
            load_json(orders / "broker_submission.json")
            if (orders / "broker_submission.json").is_file()
            else {}
        )
        reconciliation = (
            load_json(orders / "reconciliation.json")
            if (orders / "reconciliation.json").is_file()
            else {}
        )
        return broker, reconciliation

    def _latest_log_status(
        self,
    ) -> dict[str, Any]:
        files = [
            path
            for path in self.paths.logs.glob("*.log")
            if path.is_file()
        ]
        if not files:
            return {
                "path": None,
                "updated_at": None,
                "age_seconds": None,
            }
        latest = max(
            files,
            key=lambda path: path.stat().st_mtime,
        )
        modified = datetime.fromtimestamp(
            latest.stat().st_mtime,
            tz=timezone.utc,
        )
        return {
            "path": str(latest),
            "updated_at": modified.isoformat(),
            "age_seconds": round(
                max(
                    0.0,
                    (
                        datetime.now(timezone.utc)
                        - modified
                    ).total_seconds(),
                ),
                3,
            ),
        }

    def _scheduler_status(
        self,
    ) -> dict[str, Any] | None:
        if self.profile_environment != "live":
            return None
        state = self._load_scheduler_state()
        settings = self._schedule_settings()
        sessions = self._cached_market_sessions(
            state
        )
        slots = state.get("slots", {})
        if not isinstance(slots, Mapping):
            slots = {}
        next_slot = next_schedule_slot(
            sessions,
            now=datetime.now(NEW_YORK_TZ),
            slot_records=slots,
            settings=settings,
        )
        last_slot_id = state.get("last_slot_id")
        last_slot = (
            slots.get(last_slot_id)
            if last_slot_id is not None
            else None
        )
        return {
            "timezone": settings.timezone,
            "calendar_fetched_at": state.get(
                "calendar_fetched_at"
            ),
            "calendar_error": state.get(
                "calendar_error"
            ),
            "calendar_failure_count": state.get(
                "calendar_failure_count", 0
            ),
            "calendar_retry_not_before": state.get(
                "calendar_retry_not_before"
            ),
            "next_slot": (
                next_slot.to_dict()
                if next_slot is not None
                else None
            ),
            "last_slot": (
                dict(last_slot)
                if isinstance(last_slot, Mapping)
                else None
            ),
            "last_status": state.get("last_status"),
            "service_status": state.get(
                "service_status"
            ),
            "last_exit_code": state.get(
                "last_exit_code"
            ),
            "close_check_status": state.get(
                "close_check_status"
            ),
            "state_path": str(
                self.paths.scheduler_state
            ),
            "monitor_log_directory": str(
                self.paths.monitor_logs
            ),
        }

    def status(self) -> dict[str, Any]:
        current = self._current_document()
        previous = self._previous_document()
        release_valid = False
        if current is not None:
            try:
                self._release_from_document(current)
                release_valid = True
            except Exception:
                release_valid = False
        service = self.launchd.status()
        latest = self._latest_cycle_state()
        state_path = latest[0] if latest else None
        state = latest[1] if latest else {}
        broker, reconciliation = self._latest_submission(
            state_path
        )
        binding_hash = "unknown"
        binding = self._binding_path()
        if binding.is_file():
            try:
                binding_hash = str(
                    load_json(binding).get(
                        "account_id_hash", "unknown"
                    )
                )[:12]
            except Exception:
                pass
        config_root = self.paths.project_root
        if current is not None:
            config_root = Path(
                str(current["release_path"])
            )
        profile: dict[str, Any] = {}
        submission_policy: dict[str, Any] = {}
        try:
            profile = load_json(
                config_root
                / "config/v2/profiles"
                / f"{self.profile_id}.json"
            )
            policy_reference = str(
                profile.get(
                    "submission_policy", ""
                )
            )
            policy_id, separator, policy_version = (
                policy_reference.partition("@")
            )
            submission_policy = load_json(
                config_root
                / "config/v2/submission_policies/"
                / (
                    f"{policy_id}-{policy_version}.json"
                    if separator
                    else "__invalid__.json"
                )
            )
        except Exception:
            pass
        switches = submission_policy.get(
            "deployment_switches", {}
        )
        summary = reconciliation.get("summary", {})
        scheduler = self._scheduler_status()
        current_stage = None
        stages = state.get("stages")
        if isinstance(stages, Mapping):
            current_step = str(
                state.get("current_step", "")
            )
            stage_key = {
                "RUN_COARSE": "coarse",
                "RUN_PORTFOLIO": "portfolio",
                "RUN_EXECUTION": "execution",
                "BUILD_ORDERS": "orders",
                "SUBMIT_ORDERS": "orders",
                "WRITE_REPORT": "report",
            }.get(current_step)
            if stage_key is not None:
                value = stages.get(stage_key)
                if isinstance(value, Mapping):
                    current_stage = {
                        "name": stage_key,
                        "status": value.get("status"),
                        "attempts": value.get(
                            "attempts"
                        ),
                        "message": value.get("message"),
                    }
        next_run = None
        if (
            scheduler is not None
            and isinstance(
                scheduler.get("next_slot"),
                Mapping,
            )
        ):
            next_run = scheduler["next_slot"].get(
                "scheduled_at"
            )
        elif (
            self.profile_environment != "live"
            and service.get("loaded")
        ):
            next_run = (
                f"within {SERVICE_INTERVAL_SECONDS}s"
            )
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "environment": self.profile_environment,
            "account_hash_prefix": binding_hash,
            "current_release": (
                current.get("release_id")
                if current
                else None
            ),
            "release_valid": release_valid,
            "previous_release": (
                previous.get("release_id")
                if previous
                else None
            ),
            "strategy": (
                f"{profile.get('strategy', {}).get('strategy_id')}@"
                f"{profile.get('strategy', {}).get('strategy_version')}"
                if profile
                else None
            ),
            "risk_profile": profile.get("risk_profile"),
            "order_policy": profile.get("order_policy"),
            "submission_policy": profile.get(
                "submission_policy"
            ),
            "service": service,
            "trading_enabled": bool(
                current
                and current.get("trading_enabled")
            ),
            "emergency_stop": bool(
                isinstance(switches, dict)
                and switches.get("emergency_stop")
            ),
            "last_cycle": state.get("cycle_id"),
            "last_cycle_status": state.get("status"),
            "last_cycle_updated_at": state.get(
                "updated_at"
            ),
            "last_cycle_step": state.get(
                "current_step"
            ),
            "last_cycle_stage": current_stage,
            "last_submit_count": int(
                broker.get("submitted_count", 0)
            ),
            "filled_orders": int(
                summary.get("filled", 0)
                if isinstance(summary, dict)
                else 0
            ),
            "partially_filled_orders": int(
                summary.get("partially_filled", 0)
                if isinstance(summary, dict)
                else 0
            ),
            "open_orders": int(
                summary.get("open", 0)
                if isinstance(summary, dict)
                else 0
            ),
            "rejected_orders": int(
                summary.get("rejected", 0)
                if isinstance(summary, dict)
                else 0
            ),
            "uncertain_operations": int(
                broker.get("uncertain_count", 0)
            ),
            "run_lock": inspect_process_lock(
                self.paths.run_lock
            ),
            "scheduler_lock": inspect_process_lock(
                self.paths.scheduler_lock
            ),
            "last_log": self._latest_log_status(),
            "scheduler": scheduler,
            "next_run": next_run,
        }

    def health(self) -> dict[str, Any]:
        status = self.status()
        reasons: list[str] = []
        if status["uncertain_operations"] > 0:
            health = "blocked"
            reasons.append("submission_uncertain")
        elif (
            isinstance(status.get("scheduler"), Mapping)
            and status["scheduler"].get("last_status")
            == "blocked_uncertain"
        ):
            health = "blocked"
            reasons.append("scheduler_submission_uncertain")
        elif status["emergency_stop"]:
            health = "blocked"
            reasons.append("emergency_stop")
        elif not status["current_release"]:
            health = "unhealthy"
            reasons.append("no_current_release")
        elif not status["release_valid"]:
            health = "unhealthy"
            reasons.append("release_manifest_invalid")
        elif not status["service"]["loaded"]:
            health = "unhealthy"
            reasons.append("service_not_loaded")
        elif (
            self.profile_environment == "live"
            and not status["trading_enabled"]
        ):
            health = "unhealthy"
            reasons.append("live_trading_not_enabled")
        elif (
            status["run_lock"]["alive"]
            and float(
                status["run_lock"].get(
                    "age_seconds", 0
                )
                or 0
            )
            > 45 * 60
        ):
            health = "degraded"
            reasons.append("live_cycle_stalled")
        elif (
            isinstance(status.get("scheduler"), Mapping)
            and (
                status["scheduler"].get(
                    "last_status"
                )
                in {
                    "state_invalid",
                    "failed_closed",
                }
                or status["scheduler"].get(
                    "calendar_error"
                )
                is not None
            )
        ):
            health = "degraded"
            reasons.append(
                "scheduler_requires_attention"
            )
        elif (
            isinstance(status.get("scheduler"), Mapping)
            and status["scheduler"].get(
                "close_check_status"
            )
            == "display_sleep_failed"
        ):
            health = "degraded"
            reasons.append("display_sleep_failed")
        elif (
            status["last_cycle_status"]
            not in NORMAL_TERMINAL_STATUSES
        ):
            health = "degraded"
            reasons.append(
                "no_recent_normal_terminal_cycle"
            )
        else:
            health = "healthy"
        return {
            "schema_version": "1.0",
            "status": health,
            "reasons": reasons,
            "checked_at": _utc_now(),
            "details": status,
        }

    def logs(
        self,
        *,
        follow: bool,
        lines: int = 200,
    ) -> ExitCode:
        files = sorted(self.paths.logs.glob("*.log"))
        if not files:
            self._print("暂无日志")
            return ExitCode.SUCCESS
        path = files[-1]
        secrets = dotenv_secret_values(
            self.paths.dotenv
        )
        content = path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        for line in content[-lines:]:
            self._print(
                redact_text(
                    line,
                    secret_values=secrets,
                )
            )
        if not follow:
            return ExitCode.SUCCESS
        process = subprocess.Popen(
            ["tail", "-n", "0", "-f", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self._print(
                    redact_text(
                        line.rstrip("\n"),
                        secret_values=secrets,
                    )
                )
        except KeyboardInterrupt:
            process.terminate()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return ExitCode.SUCCESS

    def rollback(self) -> dict[str, Any]:
        lock = ProcessLock(
            self.paths.deploy_lock,
            "rollback",
        )
        try:
            lock.acquire()
        except LockAlreadyHeldError as error:
            raise DeploymentError(str(error)) from error
        try:
            current = self._current_document()
            previous = self._previous_document()
            if current is None or previous is None:
                raise DeploymentError(
                    "rollback需要current和previous release"
                )
            self._release_from_document(previous)
            self.launchd.stop()
            atomic_write_json(
                self.paths.current,
                dict(previous),
            )
            atomic_write_json(
                self.paths.previous,
                dict(current),
            )
            self.launchd.install_and_start()
            health = self.health()
            if health["status"] not in {
                "healthy",
                "degraded",
            }:
                self.launchd.stop()
                atomic_write_json(
                    self.paths.current,
                    dict(current),
                )
                atomic_write_json(
                    self.paths.previous,
                    dict(previous),
                )
                self.launchd.install_and_start()
                raise DeploymentError(
                    "rollback后health检查失败，已恢复原current"
                )
            self._save_history(
                "rollback",
                {
                    "from": current,
                    "to": previous,
                    "health": health,
                    "orders_untouched": True,
                    "runtime_untouched": True,
                },
            )
            return {
                "rolled_back": True,
                "current": previous,
                "previous": current,
                "health": health,
                "orders_untouched": True,
                "runtime_untouched": True,
            }
        finally:
            lock.release()
