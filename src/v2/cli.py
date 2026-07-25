"""WA Trader v2 命令行合同。

作用：集中定义 profile、initial guidance、复查、交易许可和轮次模式参数。
重要性：这里是所有运行身份与人工输入的第一道校验边界，冲突参数必须在写入状态前失败。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Sequence


SCRIPT_VERSION = "2026-07-25-v2-cli-stage-c5-v1"


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
    profile: str = "default"
    guidance: str | None = None
    no_guidance: bool = True
    unattended: bool = False
    bind_account: bool = False

    @property
    def no_need_review(self) -> bool:
        """Compatibility accessor for Phase A callers."""
        return self.no_review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "WA Trader v2 主流程。默认使用paper模式。"
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
        required=True,
        help=(
            "账户配置名称；可显式使用default兼容旧凭据环境变量"
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
            "允许后续阶段提交Alpaca paper订单；"
            "阶段B仍不会真正提交"
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
        help="显式使用paper交易模式",
    )

    mode_group.add_argument(
        "--live",
        action="store_true",
        help=(
            "请求真实账户模式；"
            "v2初期将拒绝执行"
        ),
    )

    return parser


def parse_cli_args(
    argv: Sequence[str] | None = None,
) -> CLIOptions:
    parser = build_parser()
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

    paper = not args.live
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
        live=args.live,
        profile=profile,
        guidance=args.guidance,
        no_guidance=no_guidance,
        unattended=bool(args.unattended),
        bind_account=bool(args.bind_account),
    )
