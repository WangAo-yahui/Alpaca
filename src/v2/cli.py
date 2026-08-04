"""WA Trader v2 命令行合同。

作用：集中定义 profile、运行环境、initial guidance、复查、交易许可和轮次模式参数。
重要性：这里是 Paper/Live 运行身份与人工输入的第一道校验边界，环境必须由 profile 决定，冲突参数必须在写入状态前失败。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from v2.runtime import (
    get_project_root,
    load_json_object,
)


SCRIPT_VERSION = "2026-07-27-v2-live-cli-v1"


@dataclass(frozen=True)
class CLIOptions:
    run_date: str | None
    cycle_id: str | None
    no_review: bool
    allow_trade: bool

    force_full: bool
    force_rebalance: bool
    execution_only: bool
    maintenance_only: bool

    new_cycle: bool
    paper: bool
    live: bool

    # Defaults keep direct programmatic Stage A-C callers compatible.  The
    # command-line parser deliberately defaults to no explicit guidance so a
    # non-interactive invocation must opt in to --no-guidance/--unattended.
    # Direct programmatic callers keep the historical fixture default. The
    # command-line parser reads system.json, whose production default is live1.
    profile: str = "paper1"
    guidance: str | None = None
    no_guidance: bool = True
    unattended: bool = False
    bind_account: bool = False

    @property
    def no_need_review(self) -> bool:
        """Compatibility accessor for Phase A callers."""
        return self.no_review


def configured_default_profile(
    *,
    project_root: Path | None = None,
) -> str:
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else get_project_root()
    )
    payload = load_json_object(
        root / "config" / "v2" / "system.json"
    )
    profile = str(
        payload.get("default_profile", "")
    ).strip()
    if not profile or profile == "live":
        raise ValueError(
            "default_profile必须配置为具体profile名称"
        )
    return profile


def configured_profile_environment(
    profile_id: str,
    *,
    project_root: Path | None = None,
) -> str:
    """Read the non-secret broker environment pinned by one profile."""

    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else get_project_root()
    )
    payload = load_json_object(
        root
        / "config"
        / "v2"
        / "profiles"
        / f"{profile_id}.json"
    )
    environment = str(
        payload.get("environment", "")
    ).strip().lower()
    if environment not in {"paper", "live"}:
        raise ValueError(
            f"profile环境无效：{profile_id}"
        )
    return environment


def build_parser(
    *,
    project_root: Path | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "WA Trader v2 主流程。当前默认使用live1"
            "和独立.env_live。"
        )
    )

    parser.add_argument(
        "--run-date",
        help=(
            "指定纽约运行日期，格式YYYY-MM-DD；"
            "默认使用当前纽约日期"
        ),
    )

    parser.add_argument(
        "--profile",
        default=configured_default_profile(
            project_root=project_root
        ),
        help=(
            "账户配置名称；默认读取system.json.default_profile"
        ),
    )

    parser.add_argument(
        "--cycle-id",
        help=(
            "恢复指定轮次，格式YYYYMMDDTHHMMSS"
        ),
    )

    parser.add_argument(
        "--new-cycle",
        action="store_true",
        help=(
            "忽略可恢复的active cycle，"
            "强制创建新轮次"
        ),
    )

    parser.add_argument(
        "--no-review",
        "--no-need-review",
        "--no_need_review",
        dest="no_review",
        action="store_true",
        help=(
            "第二阶段后不等待人工意见，"
            "自动继续"
        ),
    )

    guidance_group = (
        parser.add_mutually_exclusive_group()
    )
    guidance_group.add_argument(
        "--guidance",
        help="启动时提供贯穿粗选、组合和执行阶段的研究建议",
    )
    guidance_group.add_argument(
        "--no-guidance",
        "--no_initial_guidance",
        dest="no_guidance",
        action="store_true",
        help="明确跳过启动建议",
    )

    parser.add_argument(
        "--unattended",
        action="store_true",
        help=(
            "无人值守运行，等价于"
            "--no-guidance --no-review"
        ),
    )

    parser.add_argument(
        "--bind-account",
        action="store_true",
        help="显式确认并首次绑定当前Alpaca账户hash",
    )

    parser.add_argument(
        "--allow-trade",
        "--allow_trade",
        dest="allow_trade",
        action="store_true",
        help=(
            "允许后续阶段提交当前profile对应的"
            "Alpaca订单；仍需通过全部硬风控"
        ),
    )

    parser.add_argument(
        "--force-full",
        action="store_true",
        help=(
            "强制执行当天完整调查，"
            "包括第一阶段"
        ),
    )

    parser.add_argument(
        "--force-rebalance",
        action="store_true",
        help=(
            "复用当天候选池，"
            "强制重新运行第二阶段"
        ),
    )

    parser.add_argument(
        "--execution-only",
        action="store_true",
        help=(
            "复用现有组合方案，"
            "只刷新执行数据并运行第三阶段"
        ),
    )

    parser.add_argument(
        "--maintenance-only",
        action="store_true",
        help=(
            "只维护历史订单和日报，"
            "不运行新的交易决策"
        ),
    )

    mode_group = parser.add_mutually_exclusive_group()

    mode_group.add_argument(
        "--paper",
        action="store_true",
        help="断言所选profile属于paper环境",
    )

    mode_group.add_argument(
        "--live",
        action="store_true",
        help=(
            "选择live1，或断言显式profile属于live环境"
        ),
    )

    return parser


def parse_cli_args(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
) -> CLIOptions:
    parser = build_parser(
        project_root=project_root
    )
    args = parser.parse_args(argv)

    if args.cycle_id and args.new_cycle:
        parser.error(
            "--cycle-id与--new-cycle不能同时使用"
        )

    selected_cycle_modes = [
        name
        for name, selected in (
            ("--force-full", args.force_full),
            (
                "--force-rebalance",
                args.force_rebalance,
            ),
            (
                "--execution-only",
                args.execution_only,
            ),
            (
                "--maintenance-only",
                args.maintenance_only,
            ),
        )
        if selected
    ]
    if len(selected_cycle_modes) > 1:
        parser.error(
            "轮次模式参数不能同时使用："
            + "、".join(selected_cycle_modes)
        )

    if (
        args.allow_trade
        and args.maintenance_only
    ):
        parser.error(
            "--allow-trade不能与"
            "--maintenance-only同时使用"
        )

    if args.guidance and args.unattended:
        parser.error(
            "--guidance不能与--unattended同时使用"
        )

    profile = str(args.profile).strip()
    if not profile:
        parser.error("--profile不能为空")
    default_profile = configured_default_profile(
        project_root=project_root
    )
    if args.live and profile == default_profile:
        profile = "live1"
    try:
        environment = configured_profile_environment(
            profile,
            project_root=project_root,
        )
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    if args.paper and environment != "paper":
        parser.error(
            "--paper与所选profile环境不一致"
        )
    paper = (
        False
        if args.live
        else environment == "paper"
    )
    live = (
        True
        if args.live
        else environment == "live"
    )
    no_review = bool(
        args.no_review or args.unattended
    )
    no_guidance = bool(
        args.no_guidance or args.unattended
    )

    return CLIOptions(
        run_date=args.run_date,
        cycle_id=args.cycle_id,
        no_review=no_review,
        allow_trade=args.allow_trade,
        force_full=args.force_full,
        force_rebalance=(
            args.force_rebalance
        ),
        execution_only=(
            args.execution_only
        ),
        maintenance_only=(
            args.maintenance_only
        ),
        new_cycle=args.new_cycle,
        paper=paper,
        live=live,
        profile=profile,
        guidance=args.guidance,
        no_guidance=no_guidance,
        unattended=bool(args.unattended),
        bind_account=bool(args.bind_account),
    )
