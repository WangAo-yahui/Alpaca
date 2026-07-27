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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from v2.deployment.constants import (
    NORMAL_TERMINAL_STATUSES,
    PROFILE_ID,
    SERVICE_INTERVAL_SECONDS,
    STRATEGY_ID,
    STRATEGY_VERSION,
    ExitCode,
)
from v2.deployment.launchd import LaunchdController
from v2.deployment.locks import (
    LockAlreadyHeldError,
    ProcessLock,
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
)


class DeploymentError(RuntimeError):
    """A public, credential-free deployment failure."""


class DeploymentSafetyBlocked(DeploymentError):
    """A deploy or run request rejected by an explicit safety gate."""


CommandRunner = Callable[
    ..., subprocess.CompletedProcess[str]
]


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
        home: Path | None = None,
        runner: CommandRunner = subprocess.run,
        platform_name: str | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.paths = DeploymentPaths.for_project(
            project_root,
            home=home,
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
        key_present = bool(
            {
                "ALPACA_API_KEY",
                "APCA_API_KEY_ID",
            }
            & (names | set(os.environ))
        )
        secret_present = bool(
            {
                "ALPACA_SECRET_KEY",
                "APCA_API_SECRET_KEY",
            }
            & (names | set(os.environ))
        )
        return key_present and secret_present

    def _binding_path(self) -> Path:
        shared = (
            self.paths.runtime
            / "account_bindings"
            / f"{PROFILE_ID}.json"
        )
        source = (
            self.paths.project_root
            / "account_bindings"
            / f"{PROFILE_ID}.json"
        )
        return shared if shared.is_file() else source

    def doctor(self) -> dict[str, Any]:
        profile_path = (
            self.paths.project_root
            / "config"
            / "v2"
            / "profiles"
            / "paper1.json"
        )
        submission_path = (
            self.paths.project_root
            / "config"
            / "v2"
            / "submission_policies"
            / "alpaca_paper-1.0.0.json"
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
                    payload.get("profile_id") == PROFILE_ID
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
                "name": "paper1_profile",
                "ok": (
                    profile.get("profile_id") == PROFILE_ID
                    and profile.get("environment") == "paper"
                    and profile.get("enabled") is True
                    and profile.get("submission_policy")
                    == "alpaca_paper@1.0.0"
                ),
                "detail": "paper only",
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
                    submission.get("environment") == "paper"
                    and submission.get("allow_submit") is True
                    and submission.get("allow_direct_replace") is False
                    and isinstance(
                        submission.get("deployment_switches"),
                        dict,
                    )
                    and submission["deployment_switches"].get(
                        "live_submission_enabled"
                    )
                    is False
                ),
                "detail": "paper write gate",
            },
            {
                "name": "live_disabled",
                "ok": (
                    profile.get("environment") == "paper"
                    and submission.get("environment") == "paper"
                ),
                "detail": "live rejected",
            },
        ]
        return {
            "schema_version": "1.0",
            "profile_id": PROFILE_ID,
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
            / "paper1.json"
        )
        target = (
            self.paths.runtime
            / "account_bindings"
            / "paper1.json"
        )
        if not source.is_file() and not target.is_file():
            raise DeploymentError(
                "paper1账户绑定不存在"
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
                        "共享账户绑定与现有paper1不一致"
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
            "profile_id": PROFILE_ID,
            "environment": "paper",
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
            payload.get("profile_id") == PROFILE_ID
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
            if enable_trading and not self._trading_deploy_verified():
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
            "accounts/paper1/strategies/"
            f"{STRATEGY_ID}/{STRATEGY_VERSION}/"
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
        environment = self.paths.application_environment(
            git_commit=git_commit
        )
        command = [
            str(self.paths.venv_python),
            "-u",
            str(application_root / "src" / "v2" / "main.py"),
            "--profile",
            PROFILE_ID,
            "--unattended",
        ]
        if allow_trade:
            command.append("--allow-trade")
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
                "profile_id": PROFILE_ID,
                "cycle_id": state.get("cycle_id"),
                "submitted_count": submitted,
                "uncertain_count": uncertain,
                "reconciled": True,
                "verified_at": _utc_now(),
            },
        )

    def run(self, *, allow_trade: bool) -> ExitCode:
        current = self._current_document()
        if current is None:
            root = self.paths.project_root
            commit = self._git_commit()
        else:
            artifact = self._release_from_document(
                current
            )
            root = artifact.root
            commit = artifact.git_commit
        return self._run_application(
            application_root=root,
            git_commit=commit,
            allow_trade=allow_trade,
            command_name=(
                "manual-paper"
                if allow_trade
                else "manual-dry-run"
            ),
        )

    def service_run(self) -> ExitCode:
        current = self._current_document()
        if current is None:
            return ExitCode.CONFIGURATION_ERROR
        artifact = self._release_from_document(current)
        return self._run_application(
            application_root=artifact.root,
            git_commit=artifact.git_commit,
            allow_trade=bool(
                current.get("trading_enabled")
            ),
            command_name="launchd-paper1",
        )

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
                / "config/v2/profiles/paper1.json"
            )
            submission_policy = load_json(
                config_root
                / "config/v2/submission_policies/"
                "alpaca_paper-1.0.0.json"
            )
        except Exception:
            pass
        switches = submission_policy.get(
            "deployment_switches", {}
        )
        summary = reconciliation.get("summary", {})
        return {
            "schema_version": "1.0",
            "profile_id": PROFILE_ID,
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
            "last_submit_count": int(
                broker.get("submitted_count", 0)
            ),
            "open_orders": int(
                summary.get("open", 0)
                if isinstance(summary, dict)
                else 0
            ),
            "uncertain_operations": int(
                broker.get("uncertain_count", 0)
            ),
            "next_run": (
                f"within {SERVICE_INTERVAL_SECONDS}s"
                if service.get("loaded")
                else None
            ),
        }

    def health(self) -> dict[str, Any]:
        status = self.status()
        reasons: list[str] = []
        if status["uncertain_operations"] > 0:
            health = "blocked"
            reasons.append("submission_uncertain")
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
