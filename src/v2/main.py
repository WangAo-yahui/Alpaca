from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


# 支持直接运行：
# python3 -u src/v2/main.py
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    src_root = project_root / "src"

    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


from v2.cli import CLIOptions, parse_cli_args  # noqa: E402
from v2.config import V2Config, load_config  # noqa: E402
from v2.data.alpaca_client import (  # noqa: E402
    AlpacaClients,
    create_alpaca_clients,
)
from v2.data.snapshots import (  # noqa: E402
    BaseSnapshotResult,
    create_base_snapshot,
)
from v2.data.daily_bars import (  # noqa: E402
    DailyBarStore,
)
from v2.exceptions import (  # noqa: E402
    LiveTradingRejected,
    TemporaryDataError,
    V2Error,
)
from v2.models.state import (  # noqa: E402
    CoarseStatus,
    CycleKind,
    CycleState,
    CycleStatus,
    DailyState,
    RESUMABLE_CYCLE_STATUSES,
    TERMINAL_CYCLE_STATUSES,
    ReviewMode,
    StepName,
    initialize_cycle_state,
    initialize_daily_state,
    load_daily_state,
    load_cycle_state,
    register_cycle,
    save_cycle_state,
    save_daily_state,
    update_invocation,
)
from v2.runtime import (  # noqa: E402
    CyclePaths,
    atomic_write_json,
    build_cycle_paths,
    build_daily_paths,
    create_cycle_paths,
    ensure_cycle_directories,
    new_york_now_iso,
    normalize_cycle_id,
    normalize_run_date,
    utc_now_iso,
)
from v2.state_machine import (  # noqa: E402
    CycleKindInputs,
    begin_next_step,
    complete_current_step,
    decide_cycle_kind,
    fail_current_step,
    mark_daily_cycle_terminal,
    next_step,
    prepare_state,
    validate_resume_compatibility,
)
from v2.stages.coarse import (  # noqa: E402
    CoarseRunner,
    CoarseStageResult,
    execute_coarse_selection,
)


SCRIPT_VERSION = "2026-07-24-v2-stage-c-v1"


@dataclass(frozen=True)
class CycleResolution:
    paths: CyclePaths
    state: CycleState
    resumed: bool
    reason: str


@dataclass(frozen=True)
class StageBRunResult:
    resolution: CycleResolution
    snapshot: BaseSnapshotResult | None
    data_refreshed: bool
    stopped_at: StepName | None


@dataclass(frozen=True)
class StageCRunResult:
    resolution: CycleResolution
    base_result: StageBRunResult
    coarse: CoarseStageResult | None
    stopped_at: StepName | None


def decide_new_cycle_kind(
    daily_state: DailyState,
    options: CLIOptions,
) -> CycleKind:
    """
    只做启动时的初步分类。

    后续拿到账户、持仓、挂单和订单结果后，
    状态机仍可将execution_refresh升级为
    intraday_rebalance。
    """
    return decide_cycle_kind(
        daily_state,
        CycleKindInputs(
            force_full=options.force_full,
            force_rebalance=(
                options.force_rebalance
            ),
            execution_only=(
                options.execution_only
            ),
            maintenance_only=(
                options.maintenance_only
            ),
        ),
    )


def determine_review_mode(
    options: CLIOptions,
) -> ReviewMode:
    if options.no_review:
        return ReviewMode.SKIPPED_BY_FLAG

    return ReviewMode.PROMPT


def create_skipped_review_file(
    paths: CyclePaths,
) -> None:
    """
    无人值守模式下提前建立空人工意见文件。

    后续第二阶段结束时无需暂停。
    """
    if paths.user_review.exists():
        return

    atomic_write_json(
        paths.user_review,
        {
            "schema_version": "1.0",
            "run_date": paths.run_date,
            "cycle_id": paths.cycle_id,
            "created_at": utc_now_iso(),
            "created_at_new_york": (
                new_york_now_iso()
            ),
            "mode": "skipped_by_flag",
            "raw_comment": "",
            "constraints": [],
            "prohibitions": [],
            "preferences": [],
            "trade_requests": [],
        },
    )


def load_resumable_cycle(
    *,
    run_date: str,
    cycle_id: str,
    project_root: Path | None = None,
) -> CycleResolution | None:
    """
    轮次存在、状态可恢复时返回该轮次。

    轮次目录存在但状态尚未初始化时，
    返回None，由调用方初始化。
    """
    normalized_cycle_id = normalize_cycle_id(
        cycle_id
    )

    paths = build_cycle_paths(
        cycle_id=normalized_cycle_id,
        run_date=run_date,
        project_root=project_root,
    )

    if not paths.cycle_directory.exists():
        return None

    if not paths.cycle_state.exists():
        return None

    state = load_cycle_state(
        paths.cycle_state
    )

    if (
        state.status
        in RESUMABLE_CYCLE_STATUSES
        and state.resume_allowed
    ):
        return CycleResolution(
            paths=paths,
            state=state,
            resumed=True,
            reason=(
                "轮次状态允许恢复"
            ),
        )

    return None


def initialize_existing_empty_cycle(
    *,
    run_date: str,
    cycle_id: str,
    daily_state: DailyState,
    options: CLIOptions,
    config: V2Config,
    project_root: Path | None = None,
) -> CycleResolution:
    """
    初始化通过runtime.py手动创建但尚无状态的轮次。
    """
    paths = build_cycle_paths(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
    )

    if not paths.cycle_directory.exists():
        raise FileNotFoundError(
            f"指定轮次目录不存在：{paths.cycle_directory}"
        )

    if paths.cycle_state.exists():
        state = load_cycle_state(
            paths.cycle_state
        )

        if (
            state.status
            not in RESUMABLE_CYCLE_STATUSES
            or not state.resume_allowed
        ):
            raise RuntimeError(
                "指定轮次已经结束，不能恢复："
                f"{cycle_id}；状态={state.status.value}"
            )

        return CycleResolution(
            paths=paths,
            state=state,
            resumed=True,
            reason="显式恢复指定轮次",
        )

    cycle_kind = decide_new_cycle_kind(
        daily_state,
        options,
    )

    state = initialize_cycle_state(
        paths,
        cycle_kind=cycle_kind,
        review_mode=determine_review_mode(
            options
        ),
        previous_cycle_id=(
            daily_state.latest_cycle_id
        ),
        config_version=config.config_version,
        config_signature=config.signature,
        no_review=options.no_review,
        allow_trade=options.allow_trade,
        paper=options.paper,
        live=options.live,
    )

    return CycleResolution(
        paths=paths,
        state=state,
        resumed=False,
        reason=(
            "初始化已存在但尚无状态的轮次目录"
        ),
    )


def resolve_cycle(
    *,
    run_date: str,
    daily_state: DailyState,
    options: CLIOptions,
    config: V2Config,
    project_root: Path | None = None,
) -> CycleResolution:
    """
    优先级：

    1. 显式 --cycle-id；
    2. 可恢复的active cycle；
    3. 创建新轮次。
    """
    if options.cycle_id is not None:
        return initialize_existing_empty_cycle(
            run_date=run_date,
            cycle_id=options.cycle_id,
            daily_state=daily_state,
            options=options,
            config=config,
            project_root=project_root,
        )

    if (
        not options.new_cycle
        and daily_state.active_cycle_id
        is not None
    ):
        active = load_resumable_cycle(
            run_date=run_date,
            cycle_id=(
                daily_state.active_cycle_id
            ),
            project_root=project_root,
        )

        if active is not None:
            return active

    paths = create_cycle_paths(
        run_date,
        project_root=project_root,
    )

    cycle_kind = decide_new_cycle_kind(
        daily_state,
        options,
    )

    state = initialize_cycle_state(
        paths,
        cycle_kind=cycle_kind,
        review_mode=determine_review_mode(
            options
        ),
        previous_cycle_id=(
            daily_state.latest_cycle_id
        ),
        config_version=config.config_version,
        config_signature=config.signature,
        no_review=options.no_review,
        allow_trade=options.allow_trade,
        paper=options.paper,
        live=options.live,
    )

    return CycleResolution(
        paths=paths,
        state=state,
        resumed=False,
        reason="创建新的主函数运行轮次",
    )


def apply_resume_invocation(
    resolution: CycleResolution,
    options: CLIOptions,
) -> None:
    """
    恢复轮次时记录本次调用。

    显式的无人值守参数可以将尚未完成的
    prompt模式切换为skipped_by_flag。
    """
    state = resolution.state

    if not resolution.resumed:
        return

    state.resume_count += 1
    state.updated_at = utc_now_iso()

    update_invocation(
        state,
        no_review=options.no_review,
        allow_trade=options.allow_trade,
        paper=options.paper,
        live=options.live,
    )

    save_cycle_state(
        resolution.paths.cycle_state,
        state,
    )


def _adopt_phase_a_config_identity(
    resolution: CycleResolution,
    config: V2Config,
) -> None:
    """Migrate an untouched pre-config Phase A bootstrap state once.

    Earlier partial Phase A builds created ``initialized`` states without a
    config identity.  They are safe to adopt only before any step completed.
    A started cycle must pass strict compatibility validation instead.
    """

    state = resolution.state
    legacy_identity = state.config_version in {
        "legacy-unversioned",
        "phase-a-unconfigured",
        "2026-07-23-phase-a-v1",
    }
    untouched = (
        not state.completed_steps
        and state.status == CycleStatus.INITIALIZED
        and state.current_step.value == "START"
    )

    pre_coarse = (
        StepName.RUN_COARSE
        not in state.completed_steps
        and all(
            step not in state.completed_steps
            for step in (
                StepName.RUN_PORTFOLIO,
                StepName.RUN_EXECUTION,
                StepName.BUILD_ORDERS,
                StepName.VALIDATE_ORDERS,
                StepName.SUBMIT_ORDERS,
            )
        )
        and state.status
        in RESUMABLE_CYCLE_STATUSES
    )

    if (
        legacy_identity
        and untouched
    ) or (
        state.config_version
        == config.config_version
        and state.config_signature
        != config.signature
        and pre_coarse
    ):
        state.config_version = config.config_version
        state.config_signature = config.signature
        save_cycle_state(
            resolution.paths.cycle_state,
            state,
        )
        return

    validate_resume_compatibility(
        state,
        config_version=config.config_version,
        config_signature=config.signature,
    )


def bootstrap_main(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
) -> CycleResolution:
    """
    初始化日期状态与轮次状态。

    建立经过配置校验的日期状态、轮次状态和恢复点。
    阶段A不抓行情、不调用Codex、不提交订单。
    """
    if options.live:
        raise LiveTradingRejected()

    config = load_config(
        project_root=project_root,
    )

    run_date = normalize_run_date(
        options.run_date
    )

    daily_paths = build_daily_paths(
        run_date,
        project_root=project_root,
    )

    daily_state = initialize_daily_state(
        daily_paths,
        config_version=config.config_version,
        config_signature=config.signature,
    )

    if daily_state.config_signature != config.signature:
        # A config change invalidates reusable date-level research.  Existing
        # cycle compatibility is checked independently below.
        daily_state.config_version = config.config_version
        daily_state.config_signature = config.signature
        if daily_state.coarse_status == CoarseStatus.VALID:
            daily_state.coarse_status = CoarseStatus.INVALID
            daily_state.coarse_output_path = None
            daily_state.coarse_input_signature = None

    resolution = resolve_cycle(
        run_date=run_date,
        daily_state=daily_state,
        options=options,
        config=config,
        project_root=project_root,
    )

    ensure_cycle_directories(
        resolution.paths
    )

    _adopt_phase_a_config_identity(
        resolution,
        config,
    )

    apply_resume_invocation(
        resolution,
        options,
    )

    if (
        resolution.state.review_mode
        == ReviewMode.SKIPPED_BY_FLAG
    ):
        create_skipped_review_file(
            resolution.paths
        )

    register_cycle(
        daily_state,
        resolution.state,
    )

    save_daily_state(
        daily_paths.daily_state,
        daily_state,
    )

    # 保存后重新读取可能已被apply_resume_invocation更新的状态。
    final_state = load_cycle_state(
        resolution.paths.cycle_state
    )
    prepare_state(final_state)
    save_cycle_state(
        resolution.paths.cycle_state,
        final_state,
    )

    return CycleResolution(
        paths=resolution.paths,
        state=final_state,
        resumed=resolution.resumed,
        reason=resolution.reason,
    )


def print_bootstrap_result(
    resolution: CycleResolution,
) -> None:
    state = resolution.state
    paths = resolution.paths

    print("v2主流程初始化成功")
    print(f"纽约日期：{state.run_date}")
    print(f"cycle_id：{state.cycle_id}")
    print(
        "轮次处理："
        + (
            "恢复已有轮次"
            if resolution.resumed
            else "创建/初始化新轮次"
        )
    )
    print(f"原因：{resolution.reason}")
    print(
        f"轮次类型：{state.cycle_kind.value}"
    )
    print(
        f"人工复查：{state.review_mode.value}"
    )
    print(
        f"交易模式：paper"
    )
    print(
        f"当前步骤：{state.current_step.value}"
    )
    print(
        f"轮次状态：{state.status.value}"
    )
    upcoming_step = next_step(state)
    print(
        "下一步骤："
        f"{upcoming_step.value if upcoming_step else '无'}"
    )
    print(
        f"日期状态：{paths.daily_state}"
    )
    print(
        f"轮次状态：{paths.cycle_state}"
    )
    print(
        f"轮次目录：{paths.cycle_directory}"
    )
    print()
    print(
        "阶段A基础设施已就绪。"
    )


def run_stage_b(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
) -> StageBRunResult:
    """Run implemented data steps and stop before the Codex stages."""

    resolution = bootstrap_main(
        options,
        project_root=project_root,
    )
    state = resolution.state
    paths = resolution.paths
    snapshot: BaseSnapshotResult | None = None
    data_refreshed = False

    while True:
        pending = next_step(state)
        if pending == StepName.MAINTAIN_PREVIOUS:
            begin_next_step(state)
            complete_current_step(
                state,
                skipped=True,
                message=(
                    "阶段B仅保留历史订单维护占位；"
                    "对账将在阶段F实现"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            continue

        if pending == StepName.REFRESH_BASE_DATA:
            begin_next_step(state)
            try:
                active_clients = (
                    clients
                    if clients is not None
                    else create_alpaca_clients(
                        paper=options.paper,
                        live=options.live,
                        project_root=project_root,
                    )
                )
                snapshot = create_base_snapshot(
                    paths,
                    active_clients,
                )
                if not snapshot.decision_ready:
                    raise TemporaryDataError(
                        "关键账户、持仓或订单数据不完整，"
                        "已保存快照并阻止后续决策",
                        code=(
                            "BASE_SNAPSHOT_NOT_READY"
                        ),
                    )
                complete_current_step(
                    state,
                    output_path=str(
                        paths.base_snapshot
                    ),
                    message="基础数据刷新成功",
                )
                data_refreshed = True
                save_cycle_state(
                    paths.cycle_state,
                    state,
                )
            except Exception as error:
                fail_current_step(
                    state,
                    error,
                )
                save_cycle_state(
                    paths.cycle_state,
                    state,
                )
                if (
                    state.status
                    in TERMINAL_CYCLE_STATUSES
                ):
                    daily_state = load_daily_state(
                        paths.daily_state
                    )
                    mark_daily_cycle_terminal(
                        daily_state,
                        state,
                    )
                    save_daily_state(
                        paths.daily_state,
                        daily_state,
                    )
                raise
            continue

        if pending == StepName.DECIDE_CYCLE_KIND:
            begin_next_step(state)
            complete_current_step(
                state,
                message=(
                    "阶段B完成初步轮次类型确认："
                    f"{state.cycle_kind.value}"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            continue

        return StageBRunResult(
            resolution=CycleResolution(
                paths=paths,
                state=state,
                resumed=resolution.resumed,
                reason=resolution.reason,
            ),
            snapshot=snapshot,
            data_refreshed=data_refreshed,
            stopped_at=pending,
        )


def print_stage_b_result(
    result: StageBRunResult,
) -> None:
    print_bootstrap_result(
        result.resolution
    )
    permission = (
        "enabled"
        if result.resolution.state
        .trade_permission.submission_enabled
        else "dry-run"
    )
    if result.data_refreshed:
        print("基础数据刷新成功")
        print(
            "基础快照："
            f"{result.resolution.paths.base_snapshot}"
        )
    else:
        print("本轮类型无需刷新基础数据")
    print(f"交易提交权限：{permission}")
    print(
        "当前阶段尚未实现Codex决策和下单，"
        "未提交任何订单"
    )


def run_stage_c(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
    coarse_runner: CoarseRunner | None = None,
    bar_store: DailyBarStore | None = None,
) -> StageCRunResult:
    """Run Stage B plus coarse selection, then stop at portfolio."""

    base_result = run_stage_b(
        options,
        project_root=project_root,
        clients=clients,
    )
    resolution = base_result.resolution
    state = resolution.state
    paths = resolution.paths
    coarse_result: CoarseStageResult | None = None
    pending = next_step(state)
    if pending == StepName.RUN_COARSE:
        begin_next_step(state)
        save_cycle_state(
            paths.cycle_state,
            state,
        )
        try:
            config = load_config(
                project_root=project_root,
            )
            coarse_result = (
                execute_coarse_selection(
                    paths=paths,
                    state=state,
                    options=options,
                    config=config,
                    runner=coarse_runner,
                    bar_store=bar_store,
                )
            )
            complete_current_step(
                state,
                skipped=coarse_result.reused,
                output_path=str(
                    coarse_result.output_path
                ),
                message=(
                    "复用同日有效60只粗选候选"
                    if coarse_result.reused
                    else "完成并验证60只粗选候选"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
        except Exception as error:
            fail_current_step(
                state,
                error,
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            if state.status in TERMINAL_CYCLE_STATUSES:
                daily_state = load_daily_state(
                    paths.daily_state
                )
                mark_daily_cycle_terminal(
                    daily_state,
                    state,
                )
                save_daily_state(
                    paths.daily_state,
                    daily_state,
                )
            raise

    stopped_at = next_step(state)
    return StageCRunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        base_result=base_result,
        coarse=coarse_result,
        stopped_at=stopped_at,
    )


def print_stage_c_result(
    result: StageCRunResult,
) -> None:
    print_bootstrap_result(
        result.resolution
    )
    if result.base_result.data_refreshed:
        print("基础数据刷新成功")
        print(
            "基础快照："
            f"{result.resolution.paths.base_snapshot}"
        )
    permission = (
        "enabled"
        if result.resolution.state
        .trade_permission.submission_enabled
        else "dry-run"
    )
    print(f"交易提交权限：{permission}")
    if result.coarse is None:
        print("当前轮次类型无需运行粗选")
    else:
        print(
            "第一阶段动作："
            f"{result.coarse.action}"
        )
        print(
            "粗选候选数："
            f"{len(result.coarse.output['selections'])}"
        )
        print(
            "粗选输出："
            f"{result.coarse.output_path}"
        )
        print(
            "第一阶段联网："
            f"{result.coarse.network_status}"
        )
        print("第一阶段校验：通过")
    print(
        "阶段C已停止在："
        f"{result.stopped_at.value if result.stopped_at else '无'}"
    )
    print(
        "尚未运行组合、复查、执行或下单阶段；"
        "未提交任何订单"
    )


def main() -> int:
    print(f"脚本版本：{SCRIPT_VERSION}")

    try:
        options = parse_cli_args()
        result = run_stage_c(
            options
        )
        print_stage_c_result(
            result
        )
        return 0

    except V2Error as error:
        disposition = error.disposition()
        print("v2主流程拒绝或失败")
        print(f"错误代码：{disposition.code}")
        print(f"错误信息：{disposition.message}")
        return 2

    except Exception as error:
        print("v2主流程初始化失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
