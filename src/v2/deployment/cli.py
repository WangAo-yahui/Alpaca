"""实现顶层 ``./wa`` 的 Paper/Live Stage H 命令行合同。

作用：解析固定运维子命令，将管理器结果输出为人类文本或稳定 JSON。
重要性：所有用户和 launchd 入口都必须经过同一错误映射，不能直接绕过锁或部署门禁。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from v2.deployment.constants import ExitCode
from v2.deployment.locks import LockAlreadyHeldError
from v2.deployment.manager import (
    DeploymentError,
    DeploymentManager,
    DeploymentSafetyBlocked,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wa",
        description=(
            "WA Trader v2 macOS Paper/Live 本地部署与运行工具"
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )
    def identity(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--profile",
            help="运行profile；默认paper1",
        )
        command.add_argument(
            "--live",
            action="store_true",
            help="选择live1实盘profile",
        )

    bootstrap = commands.add_parser("bootstrap")
    identity(bootstrap)
    doctor = commands.add_parser("doctor")
    identity(doctor)
    deploy = commands.add_parser("deploy")
    identity(deploy)
    deploy.add_argument(
        "--enable-trading",
        action="store_true",
        help="为所选profile启用自动交易；Paper仍要求既有提交验证",
    )
    run = commands.add_parser("run")
    identity(run)
    run.add_argument(
        "--allow-trade",
        action="store_true",
        help="人工运行一次允许当前profile提交的cycle",
    )
    run.add_argument(
        "--force-full",
        action="store_true",
        help="人工强制生成新的完整决策，但不单独授予提交权限",
    )
    run.add_argument(
        "--maintenance-only",
        action="store_true",
        help="只对账既有订单并维护日报，不生成或提交新订单",
    )
    run.add_argument(
        "--bind-account",
        action="store_true",
        help="首次运行时绑定当前profile的账户hash",
    )
    start = commands.add_parser("start")
    identity(start)
    stop = commands.add_parser("stop")
    identity(stop)
    restart = commands.add_parser("restart")
    identity(restart)
    status = commands.add_parser("status")
    identity(status)
    status.add_argument("--json", action="store_true")
    health = commands.add_parser("health")
    identity(health)
    health.add_argument("--json", action="store_true")
    logs = commands.add_parser("logs")
    identity(logs)
    logs.add_argument("--follow", action="store_true")
    rollback = commands.add_parser("rollback")
    identity(rollback)
    service_run = commands.add_parser(
        "_service-run",
        help="launchd内部防重入运行入口",
    )
    identity(service_run)
    return parser


def _print_document(
    payload: dict[str, object],
    *,
    as_json: bool,
) -> None:
    if as_json:
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for nested_key, nested_value in value.items():
                print(
                    f"  {nested_key}: {nested_value}"
                )
        elif isinstance(value, list):
            print(f"{key}:")
            for item in value:
                print(f"  - {item}")
        else:
            print(f"{key}: {value}")


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    options = build_parser().parse_args(argv)
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    profile_id = (
        str(options.profile).strip()
        if getattr(options, "profile", None)
        else (
            "live1"
            if getattr(options, "live", False)
            else "paper1"
        )
    )
    manager = DeploymentManager(
        root,
        profile_id=profile_id,
    )
    if (
        getattr(options, "live", False)
        and manager.profile_environment != "live"
    ):
        print("--live与所选profile环境不一致")
        return int(ExitCode.CONFIGURATION_ERROR)
    try:
        if options.command == "bootstrap":
            _print_document(
                manager.bootstrap(),
                as_json=False,
            )
            return int(ExitCode.SUCCESS)
        if options.command == "doctor":
            document = manager.doctor()
            _print_document(document, as_json=False)
            return int(
                ExitCode.SUCCESS
                if document["healthy"]
                else ExitCode.CONFIGURATION_ERROR
            )
        if options.command == "deploy":
            _print_document(
                manager.deploy(
                    enable_trading=bool(
                        options.enable_trading
                    )
                ),
                as_json=False,
            )
            return int(ExitCode.SUCCESS)
        if options.command == "run":
            run_arguments = {
                "allow_trade": bool(
                    options.allow_trade
                ),
                "force_full": bool(
                    options.force_full
                ),
            }
            if options.maintenance_only:
                run_arguments[
                    "maintenance_only"
                ] = True
            if options.bind_account:
                run_arguments["bind_account"] = True
            return int(
                manager.run(**run_arguments)
            )
        if options.command == "_service-run":
            return int(manager.service_run())
        if options.command == "start":
            manager.start()
            print(
                f"{manager.profile_id} launchd服务已启动"
            )
            return int(ExitCode.SUCCESS)
        if options.command == "stop":
            manager.stop()
            print(
                f"{manager.profile_id} launchd服务已停止"
            )
            return int(ExitCode.SUCCESS)
        if options.command == "restart":
            manager.restart()
            print(
                f"{manager.profile_id} launchd服务已重启"
            )
            return int(ExitCode.SUCCESS)
        if options.command == "status":
            _print_document(
                manager.status(),
                as_json=bool(options.json),
            )
            return int(ExitCode.SUCCESS)
        if options.command == "health":
            document = manager.health()
            _print_document(
                document,
                as_json=bool(options.json),
            )
            return int(
                ExitCode.SUCCESS
                if document["status"] == "healthy"
                else ExitCode.RETRIABLE_ERROR
                if document["status"] == "degraded"
                else ExitCode.SAFETY_BLOCK
                if document["status"] == "blocked"
                else ExitCode.DEPLOYMENT_ERROR
            )
        if options.command == "logs":
            return int(
                manager.logs(
                    follow=bool(options.follow)
                )
            )
        if options.command == "rollback":
            _print_document(
                manager.rollback(),
                as_json=False,
            )
            return int(ExitCode.SUCCESS)
    except DeploymentSafetyBlocked as error:
        print(f"安全门禁阻止：{error}")
        return int(ExitCode.SAFETY_BLOCK)
    except LockAlreadyHeldError as error:
        print(f"任务已在运行：{error}")
        return int(ExitCode.ALREADY_RUNNING)
    except DeploymentError as error:
        print(f"部署操作失败：{error}")
        return int(ExitCode.DEPLOYMENT_ERROR)
    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ) as error:
        print(f"配置或文件错误：{error}")
        return int(ExitCode.CONFIGURATION_ERROR)
    return int(ExitCode.DEPLOYMENT_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
