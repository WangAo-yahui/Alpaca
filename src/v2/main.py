"""WA Trader v2 Stage G Paper/Live 提交闭环主流程入口。

作用：在 Stage F 硬校验后执行写前门禁、幂等提交/取消、即时对账、cycle summary 与日报。
重要性：这是唯一可到达券商写接口的编排层；环境串用、盲重试和未校验订单始终被拒绝。
"""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from jsonschema import Draft202012Validator


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
    call_api,
    create_alpaca_clients,
)
from v2.data.account import fetch_account  # noqa: E402
from v2.data.positions import fetch_positions  # noqa: E402
from v2.data.snapshots import (  # noqa: E402
    BaseSnapshotResult,
    create_base_snapshot,
)
from v2.data.daily_bars import (  # noqa: E402
    DailyBarStore,
)
from v2.data.execution_snapshot import (  # noqa: E402
    ExecutionSnapshotResult,
    create_execution_snapshot,
)
from v2.data.pretrade_snapshot import (  # noqa: E402
    PreTradeSnapshotResult,
    create_pretrade_snapshot,
)
from v2.exceptions import (  # noqa: E402
    ConfigurationError,
    LiveTradingRejected,
    SafetyBlockedError,
    StateValidationError,
    TemporaryDataError,
    V2Error,
)
from v2.guidance import (  # noqa: E402
    collect_initial_guidance,
)
from v2.review import (  # noqa: E402
    UserReview,
    collect_user_review,
    write_skipped_review,
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
    StateMessage,
)
from v2.models.orders import (  # noqa: E402
    BrokerRequestSpec,
    ProposedOrderPlan,
    ValidatedOrderPlan,
    canonical_hash,
)
from v2.models.submission import (  # noqa: E402
    SubmissionIntent,
    SubmissionOperation,
    SubmissionOperationState,
    SubmissionOperationType,
    broker_submission_document,
)
from v2.profiles import (  # noqa: E402
    Profile,
    account_binding_path,
    load_order_policy,
    load_profile,
    load_risk_profile,
    load_submission_policy,
    verify_or_bind_account,
)
from v2.releases import (  # noqa: E402
    StrategyRelease,
    get_git_commit,
    load_strategy_release,
    sha256_file,
)
from v2.runtime import (  # noqa: E402
    CyclePaths,
    atomic_write_json,
    build_cycle_paths,
    build_daily_paths,
    create_cycle_paths,
    ensure_cycle_directories,
    load_json_object,
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
    pause_for_review,
    prepare_state,
    validate_resume_compatibility,
)
from v2.stages.coarse import (  # noqa: E402
    CoarseRunner,
    CoarseStageResult,
    execute_coarse_selection,
)
from v2.stages.portfolio import (  # noqa: E402
    PortfolioRunner,
    PortfolioStageResult,
    execute_portfolio_decision,
)
from v2.stages.execution import (  # noqa: E402
    ExecutionRunner,
    ExecutionStageResult,
    execute_execution_decision,
)
from v2.trading.order_builder import (  # noqa: E402
    build_order_plan,
)
from v2.trading.order_request_factory import (  # noqa: E402
    create_request_specs,
    request_specs_document,
)
from v2.trading.order_validator import (  # noqa: E402
    validate_order_plan,
)
from v2.trading.order_action_executor import (  # noqa: E402
    execute_cancel,
    replacement_is_unlocked,
)
from v2.trading.order_submitter import (  # noqa: E402
    request_specs_for_approved,
    submit_approved_order,
)
from v2.trading.reconciliation import (  # noqa: E402
    maintain_previous_submissions,
    reconcile_submission,
)
from v2.trading.submission_guard import (  # noqa: E402
    assert_submission_allowed,
)
from v2.trading.submission_journal import (  # noqa: E402
    SubmissionJournal,
)
from v2.reports.daily_report import (  # noqa: E402
    update_daily_report,
)
from v2.reports.natural_language_report import (  # noqa: E402
    natural_report_path,
    update_natural_language_report,
    write_fallback_natural_language_report,
    natural_report_error_path,
)


SCRIPT_VERSION = "2026-07-27-v2-natural-report-sync-v4"


@dataclass(frozen=True)
class RuntimeIdentity:
    profile: Profile
    release: StrategyRelease
    release_state: dict[str, object]
    git_verified: bool


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


@dataclass(frozen=True)
class StageDRunResult:
    resolution: CycleResolution
    stage_c_result: StageCRunResult
    portfolio: PortfolioStageResult | None
    review: UserReview | None
    stopped_at: StepName | None


@dataclass(frozen=True)
class StageERunResult:
    resolution: CycleResolution
    stage_d_result: StageDRunResult
    execution_snapshot: (
        ExecutionSnapshotResult | None
    )
    execution: ExecutionStageResult | None
    stopped_at: StepName | None


@dataclass(frozen=True)
class StageFRunResult:
    resolution: CycleResolution
    stage_e_result: StageERunResult
    pretrade_snapshot: (
        PreTradeSnapshotResult | None
    )
    proposed: ProposedOrderPlan | None
    validated: ValidatedOrderPlan | None
    request_specs: tuple[BrokerRequestSpec, ...]
    validated_document: Mapping[str, Any] | None
    stopped_at: StepName | None


@dataclass(frozen=True)
class StageGRunResult:
    resolution: CycleResolution
    stage_f_result: StageFRunResult | None
    submission_intent: Mapping[str, Any]
    broker_submission: Mapping[str, Any]
    reconciliation: Mapping[str, Any]
    cycle_summary: Mapping[str, Any]
    maintained_cycle_ids: tuple[str, ...]
    stopped_at: StepName | None


def _parse_utc_timestamp(
    value: object,
) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = (
            normalized[:-1] + "+00:00"
        )
    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    return parsed.astimezone(timezone.utc)


def _load_portfolio_for_execution(
    paths: CyclePaths,
    state: CycleState,
) -> dict[str, object]:
    """Load this cycle's portfolio or safely adopt the latest valid same-day one."""

    if paths.portfolio_output.is_file():
        return load_json_object(
            paths.portfolio_output
        )
    daily_state = load_daily_state(
        paths.daily_state
    )
    source_text = (
        daily_state
        .latest_valid_portfolio_output_path
    )
    if not source_text:
        raise SafetyBlockedError(
            "执行阶段没有可用portfolio output",
            code="EXECUTION_PORTFOLIO_MISSING",
        )
    source_path = Path(source_text)
    if (
        not source_path.is_file()
        or not source_path.resolve()
        .is_relative_to(
            paths.cycles_directory.resolve()
        )
    ):
        raise SafetyBlockedError(
            "历史portfolio路径不属于当前身份与日期",
            code=(
                "EXECUTION_PORTFOLIO_IDENTITY_MISMATCH"
            ),
        )
    source_validation = (
        source_path.parent / "validation.json"
    )
    if not source_validation.is_file():
        raise SafetyBlockedError(
            "历史portfolio缺少验证记录",
            code="EXECUTION_PORTFOLIO_INVALID",
        )
    validation = load_json_object(
        source_validation
    )
    portfolio = load_json_object(
        source_path
    )
    valid_until = _parse_utc_timestamp(
        portfolio.get("valid_until")
    )
    now = datetime.now(timezone.utc)
    if (
        validation.get("valid") is not True
        or portfolio.get("profile_id")
        != paths.profile_id
        or portfolio.get("strategy_id")
        != paths.strategy_id
        or portfolio.get("strategy_version")
        != paths.strategy_version
        or portfolio.get("run_date")
        != paths.run_date
        or valid_until is None
        or valid_until <= now
    ):
        raise SafetyBlockedError(
            "历史portfolio已过期或身份无效",
            code="EXECUTION_PORTFOLIO_INVALID",
        )
    atomic_write_json(
        paths.portfolio_output,
        portfolio,
    )
    atomic_write_json(
        paths.portfolio_reuse,
        {
            "schema_version": "1.0",
            "stage": "portfolio_decision",
            "reused": True,
            "source_cycle_id": (
                daily_state
                .latest_valid_portfolio_cycle_id
            ),
            "source_output_path": str(
                source_path
            ),
            "reused_at": utc_now_iso(),
            "reason": (
                "execution_refresh采用同日有效组合"
            ),
        },
    )
    state.reused_portfolio_cycle_id = (
        daily_state
        .latest_valid_portfolio_cycle_id
    )
    return portfolio


def load_runtime_identity(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
) -> RuntimeIdentity:
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    profile = load_profile(
        options.profile,
        project_root=root,
    )
    if (
        options.live
        != (profile.environment == "live")
        or options.paper
        != (profile.environment == "paper")
    ):
        raise ConfigurationError(
            "CLI运行环境与profile环境不一致",
            code="PROFILE_ENVIRONMENT_MISMATCH",
        )
    risk_profile = load_risk_profile(
        profile.risk_profile,
        project_root=root,
    )
    if profile.order_policy is None:
        raise ConfigurationError(
            "Stage F profile必须声明order_policy",
            code="ORDER_POLICY_REFERENCE_MISSING",
        )
    order_policy = load_order_policy(
        profile.order_policy,
        project_root=root,
    )
    if profile.submission_policy is None:
        raise ConfigurationError(
            "Stage G profile必须声明submission_policy",
            code="SUBMISSION_POLICY_REFERENCE_MISSING",
        )
    submission_policy = load_submission_policy(
        profile.submission_policy,
        project_root=root,
    )
    if (
        risk_profile.environment
        != profile.environment
    ):
        raise ConfigurationError(
            "profile与risk profile环境不一致",
            code="RISK_PROFILE_ENVIRONMENT_MISMATCH",
        )
    if (
        order_policy.environment
        != profile.environment
    ):
        raise ConfigurationError(
            "profile与order policy环境不一致",
            code="ORDER_POLICY_ENVIRONMENT_MISMATCH",
        )
    if (
        submission_policy.environment
        != profile.environment
    ):
        raise ConfigurationError(
            "profile与submission policy环境不一致",
            code="SUBMISSION_POLICY_ENVIRONMENT_MISMATCH",
        )
    release = load_strategy_release(
        profile.strategy_id,
        profile.strategy_version,
        project_root=root,
    )
    git_commit, git_verified = (
        get_git_commit(root)
    )
    return RuntimeIdentity(
        profile=profile,
        release=release,
        release_state=release.state_payload(
            risk_profile=profile.risk_profile,
            risk_profile_hash=sha256_file(
                risk_profile.source_path
            ),
            order_policy=order_policy.reference,
            order_policy_hash=sha256_file(
                order_policy.source_path
            ),
            submission_policy=(
                submission_policy.reference
            ),
            submission_policy_hash=sha256_file(
                submission_policy.source_path
            ),
            git_commit=git_commit,
            source_tree_hash=os.environ.get(
                "WA_SOURCE_TREE_HASH",
                "unknown",
            ),
            source_tree_dirty=(
                os.environ.get(
                    "WA_SOURCE_TREE_DIRTY",
                    "false",
                ).strip().lower()
                == "true"
            ),
        ),
        git_verified=git_verified,
    )


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
    """无人值守模式下提前建立可验证的空人工意见文件。"""
    if paths.user_review.exists():
        return
    write_skipped_review(paths)


def load_resumable_cycle(
    *,
    run_date: str,
    cycle_id: str,
    project_root: Path | None = None,
    profile: Profile | None = None,
    expected_release: Mapping[str, Any] | None = None,
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
        profile_id=(
            profile.profile_id
            if profile is not None
            else "default"
        ),
        strategy_id=(
            profile.strategy_id
            if profile is not None
            else "core_long"
        ),
        strategy_version=(
            profile.strategy_version
            if profile is not None
            else "1.0.0"
        ),
    )

    if not paths.cycle_directory.exists():
        return None

    if not paths.cycle_state.exists():
        return None

    state = load_cycle_state(
        paths.cycle_state
    )
    if expected_release is not None:
        identity_fields = (
            "app_version",
            "strategy_id",
            "strategy_version",
            "risk_profile",
            "risk_profile_hash",
            "order_policy",
            "order_policy_hash",
            "submission_policy",
            "submission_policy_hash",
            "release_hash",
        )
        if any(
            state.release.get(field)
            != expected_release.get(field)
            for field in identity_fields
        ):
            return None

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
    identity: RuntimeIdentity | None = None,
    project_root: Path | None = None,
) -> CycleResolution:
    """
    初始化通过runtime.py手动创建但尚无状态的轮次。
    """
    paths = build_cycle_paths(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
        profile_id=(
            identity.profile.profile_id
            if identity is not None
            else "default"
        ),
        strategy_id=(
            identity.profile.strategy_id
            if identity is not None
            else "core_long"
        ),
        strategy_version=(
            identity.profile.strategy_version
            if identity is not None
            else "1.0.0"
        ),
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
        release=(
            identity.release_state
            if identity is not None
            else None
        ),
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
    identity: RuntimeIdentity | None = None,
    project_root: Path | None = None,
) -> CycleResolution:
    """
    优先级：

    1. 显式 --cycle-id；
    2. 可恢复的active cycle；
    3. 创建新轮次。
    """
    permission_replan = False
    if options.cycle_id is not None:
        return initialize_existing_empty_cycle(
            run_date=run_date,
            cycle_id=options.cycle_id,
            daily_state=daily_state,
            options=options,
            config=config,
            identity=identity,
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
            profile=(
                identity.profile
                if identity is not None
                else None
            ),
            expected_release=(
                identity.release_state
                if identity is not None
                else None
            ),
        )

        if active is not None:
            orders_started = (
                StepName.BUILD_ORDERS
                in active.state.completed_steps
                or active.state.current_step
                in {
                    StepName.BUILD_ORDERS,
                    StepName.VALIDATE_ORDERS,
                    StepName.SUBMIT_ORDERS,
                }
            )
            permission_changed = (
                active.state.invocation.allow_trade
                != options.allow_trade
            )
            if (
                orders_started
                and permission_changed
            ):
                permission_replan = True
            else:
                return active

    paths = create_cycle_paths(
        run_date,
        project_root=project_root,
        profile_id=(
            identity.profile.profile_id
            if identity is not None
            else "default"
        ),
        strategy_id=(
            identity.profile.strategy_id
            if identity is not None
            else "core_long"
        ),
        strategy_version=(
            identity.profile.strategy_version
            if identity is not None
            else "1.0.0"
        ),
    )

    cycle_kind = (
        CycleKind.EXECUTION_REFRESH
        if permission_replan
        else decide_new_cycle_kind(
            daily_state,
            options,
        )
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
        release=(
            identity.release_state
            if identity is not None
            else None
        ),
    )

    return CycleResolution(
        paths=paths,
        state=state,
        resumed=False,
        reason=(
            "交易许可改变，创建execution refresh轮次"
            if permission_replan
            else "创建新的主函数运行轮次"
        ),
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
    if options.live and options.profile != "live1":
        raise LiveTradingRejected()

    identity = load_runtime_identity(
        options,
        project_root=project_root,
    )

    config = load_config(
        project_root=project_root,
    )

    run_date = normalize_run_date(
        options.run_date
    )

    daily_paths = build_daily_paths(
        run_date,
        project_root=project_root,
        profile_id=identity.profile.profile_id,
        strategy_id=identity.profile.strategy_id,
        strategy_version=(
            identity.profile.strategy_version
        ),
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
        identity=identity,
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

    state = resolution.state
    if state.profile_id != identity.profile.profile_id:
        raise ConfigurationError(
            "恢复轮次的profile与当前调用不一致",
            code="CYCLE_PROFILE_MISMATCH",
        )
    existing_release = state.release
    expected_release = identity.release_state
    if existing_release.get(
        "release_hash"
    ) == "unknown":
        state.release = dict(expected_release)
    else:
        release_identity_fields = (
            "app_version",
            "strategy_id",
            "strategy_version",
            "risk_profile",
            "risk_profile_hash",
            "order_policy",
            "order_policy_hash",
            "submission_policy",
            "submission_policy_hash",
            "release_hash",
            "prompt_hashes",
            "schema_hashes",
            "config_hashes",
        )
        if any(
            existing_release.get(field)
            != expected_release.get(field)
            for field in release_identity_fields
        ):
            raise ConfigurationError(
                "恢复轮次的strategy release与当前版本不一致",
                code="CYCLE_RELEASE_MISMATCH",
            )
    if (
        not identity.git_verified
        and not any(
            item.code == "GIT_COMMIT_UNKNOWN"
            for item in state.warnings
        )
    ):
        state.warnings.append(
            StateMessage(
                code="GIT_COMMIT_UNKNOWN",
                message="无法读取Git commit，已记录unknown",
            )
        )

    guidance = collect_initial_guidance(
        options,
        resolution.paths,
    )
    state.guidance = guidance.state_payload(
        resolution.paths
    )
    save_cycle_state(
        resolution.paths.cycle_state,
        state,
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
        state,
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
        "交易模式："
        + (
            "live"
            if state.invocation.live
            else "paper"
        )
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
                    "Stage G已在当前轮次前维护历史paper订单；"
                    "本步骤登记维护顺序"
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
                    else None
                )
                if active_clients is None:
                    profile = load_profile(
                        paths.profile_id,
                        project_root=project_root,
                    )
                    active_clients = (
                        create_alpaca_clients(
                            paper=options.paper,
                            live=options.live,
                            project_root=project_root,
                            profile=profile,
                        )
                    )
                    account = call_api(
                        "get_account_for_binding",
                        active_clients.trading.get_account,
                    )
                    account_id = (
                        account.get("id")
                        if isinstance(account, dict)
                        else getattr(
                            account,
                            "id",
                            None,
                        )
                    )
                    verify_or_bind_account(
                        profile,
                        account_id,
                        bind_account=(
                            options.bind_account
                        ),
                        project_root=project_root,
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


def run_stage_d(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
    coarse_runner: CoarseRunner | None = None,
    portfolio_runner: PortfolioRunner | None = None,
    bar_store: DailyBarStore | None = None,
    review_input_func: Callable[[str], str] = input,
    review_stdin: TextIO | None = None,
) -> StageDRunResult:
    """Run through portfolio and review, then stop before execution refresh."""

    stage_c_result = run_stage_c(
        options,
        project_root=project_root,
        clients=clients,
        coarse_runner=coarse_runner,
        bar_store=bar_store,
    )
    resolution = stage_c_result.resolution
    paths = resolution.paths
    state = resolution.state
    portfolio_result: (
        PortfolioStageResult | None
    ) = None
    review_result: UserReview | None = None

    if next_step(state) == StepName.RUN_PORTFOLIO:
        begin_next_step(state)
        save_cycle_state(
            paths.cycle_state,
            state,
        )
        try:
            portfolio_result = (
                execute_portfolio_decision(
                    paths=paths,
                    state=state,
                    options=options,
                    config=load_config(
                        project_root=project_root,
                    ),
                    runner=portfolio_runner,
                )
            )
            complete_current_step(
                state,
                skipped=(
                    portfolio_result.reused
                ),
                output_path=str(
                    portfolio_result.output_path
                ),
                message=(
                    "复用同日有效组合方案"
                    if portfolio_result.reused
                    else "完成并验证组合方案"
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

    if next_step(state) == StepName.COLLECT_REVIEW:
        begin_next_step(state)
        save_cycle_state(
            paths.cycle_state,
            state,
        )
        try:
            if (
                not options.no_review
                and not options.unattended
            ):
                pause_for_review(state)
                save_cycle_state(
                    paths.cycle_state,
                    state,
                )
            review_result = collect_user_review(
                options,
                paths,
                input_func=review_input_func,
                stdin=review_stdin,
            )
            complete_current_step(
                state,
                skipped=(
                    review_result.mode
                    == "skipped_by_flag"
                ),
                output_path=str(
                    paths.user_review
                ),
                message=(
                    "执行前复查已跳过"
                    if review_result.mode
                    == "skipped_by_flag"
                    else "执行前补充意见已保存"
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
    return StageDRunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        stage_c_result=stage_c_result,
        portfolio=portfolio_result,
        review=review_result,
        stopped_at=stopped_at,
    )


def print_stage_d_result(
    result: StageDRunResult,
) -> None:
    print_bootstrap_result(result.resolution)
    state = result.resolution.state
    print(
        "交易提交权限："
        + (
            "enabled"
            if state.trade_permission
            .submission_enabled
            else "dry-run"
        )
    )
    coarse = result.stage_c_result.coarse
    if coarse is not None:
        print(f"第一阶段动作：{coarse.action}")
        print(
            "候选数量："
            f"{len(coarse.output['selections'])}"
        )
    if result.portfolio is not None:
        portfolio = result.portfolio
        print(
            f"第二阶段动作：{portfolio.action}"
        )
        print(
            "目标现金比例："
            f"{portfolio.target_cash_weight:.2%}"
        )
        print(
            "目标持仓数量："
            f"{portfolio.target_symbol_count}"
        )
        print(
            "第二阶段联网："
            f"{portfolio.network_status}"
        )
        print("第二阶段校验：通过")
    if result.review is not None:
        print(
            "执行前复查："
            f"{result.review.mode}"
        )
    print(
        "下一步骤："
        f"{result.stopped_at.value if result.stopped_at else '无'}"
    )
    print("第三阶段尚未实现")
    print("提交订单数：0")
    print("未生成或提交订单")


def run_stage_e(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
    coarse_runner: CoarseRunner | None = None,
    portfolio_runner: PortfolioRunner | None = None,
    execution_runner: ExecutionRunner | None = None,
    bar_store: DailyBarStore | None = None,
    review_input_func: Callable[[str], str] = input,
    review_stdin: TextIO | None = None,
) -> StageERunResult:
    """Run through execution intent and stop before any order construction."""

    stage_d_result = run_stage_d(
        options,
        project_root=project_root,
        clients=clients,
        coarse_runner=coarse_runner,
        portfolio_runner=portfolio_runner,
        bar_store=bar_store,
        review_input_func=review_input_func,
        review_stdin=review_stdin,
    )
    resolution = stage_d_result.resolution
    paths = resolution.paths
    state = resolution.state
    snapshot_result: (
        ExecutionSnapshotResult | None
    ) = None
    execution_result: (
        ExecutionStageResult | None
    ) = None

    if (
        next_step(state)
        == StepName.REFRESH_EXECUTION_DATA
    ):
        begin_next_step(state)
        save_cycle_state(
            paths.cycle_state,
            state,
        )
        try:
            active_clients = clients
            if active_clients is None:
                profile = load_profile(
                    paths.profile_id,
                    project_root=project_root,
                )
                active_clients = (
                    create_alpaca_clients(
                        paper=options.paper,
                        live=options.live,
                        project_root=project_root,
                        profile=profile,
                    )
                )
            portfolio_output = (
                _load_portfolio_for_execution(
                    paths,
                    state,
                )
            )
            config = load_config(
                project_root=project_root,
            )
            snapshot_result = (
                create_execution_snapshot(
                    paths,
                    active_clients,
                    portfolio_output=(
                        portfolio_output
                    ),
                    minute_window=int(
                        config.market_data[
                            "minute_bar_window"
                        ]
                    ),
                )
            )
            if not snapshot_result.execution_ready:
                raise TemporaryDataError(
                    "执行级账户、持仓或订单数据不完整，"
                    "已保存快照并阻止第三阶段",
                    code=(
                        "EXECUTION_SNAPSHOT_NOT_READY"
                    ),
                )
            complete_current_step(
                state,
                output_path=str(
                    paths.execution_snapshot
                ),
                message="执行级数据刷新成功",
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
        except Exception as error:
            fail_current_step(state, error)
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            raise

    if next_step(state) == StepName.RUN_EXECUTION:
        begin_next_step(state)
        save_cycle_state(
            paths.cycle_state,
            state,
        )
        try:
            execution_result = (
                execute_execution_decision(
                    paths=paths,
                    state=state,
                    config=load_config(
                        project_root=project_root,
                    ),
                    runner=execution_runner,
                )
            )
            complete_current_step(
                state,
                output_path=str(
                    paths.execution_output
                ),
                message=(
                    "完成并验证第三阶段执行意图"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
        except Exception as error:
            fail_current_step(state, error)
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            raise

    return StageERunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        stage_d_result=stage_d_result,
        execution_snapshot=snapshot_result,
        execution=execution_result,
        stopped_at=next_step(state),
    )


def print_stage_e_result(
    result: StageERunResult,
) -> None:
    """Print the deployed identity, validations and zero-order safety stop."""

    state = result.resolution.state
    print_bootstrap_result(result.resolution)
    print(f"Profile：{state.profile_id}")
    print(
        "账户环境："
        + (
            "live"
            if state.invocation.live
            else "paper"
        )
    )
    print(
        "策略："
        f"{state.release['strategy_id']}@"
        f"{state.release['strategy_version']}"
    )
    print(
        f"风险：{state.release['risk_profile']}"
    )
    print(
        "交易提交权限："
        + (
            "enabled"
            if state.trade_permission
            .submission_enabled
            else "dry-run"
        )
    )
    coarse = (
        result.stage_d_result
        .stage_c_result.coarse
    )
    print(
        "第一阶段："
        + (
            coarse.action
            if coarse is not None
            else "not_required"
        )
    )
    portfolio = result.stage_d_result.portfolio
    print(
        "第二阶段："
        + (
            portfolio.action
            if portfolio is not None
            else "not_required"
        )
    )
    review = result.stage_d_result.review
    print(
        "执行前复查："
        + (
            review.mode
            if review is not None
            else "existing"
        )
    )
    print(
        "执行数据刷新："
        + (
            "成功"
            if result.execution_snapshot
            is not None
            else "复用当前轮次"
        )
    )
    execution = result.execution
    if execution is not None:
        print(
            f"第三阶段：{execution.action}"
        )
        print("第三阶段校验：通过")
        print(
            f"Approve：{execution.approve_count}"
        )
        print(
            f"Modify：{execution.modify_count}"
        )
        print(
            f"Defer：{execution.defer_count}"
        )
        print(
            f"Reject：{execution.reject_count}"
        )
        print(
            "No action："
            f"{execution.no_action_count}"
        )
    else:
        print("第三阶段：复用当前轮次")
    print(
        "下一步骤："
        f"{result.stopped_at.value if result.stopped_at else '无'}"
    )
    print("订单构建尚未实现")
    print("提交订单数：0")
    print("未生成、取消、替换或提交订单")


def _validate_stage_f_document(
    payload: Mapping[str, Any],
    *,
    schema_name: str,
    project_root: Path | None,
) -> None:
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    schema = load_json_object(
        root / "schemas" / "v2" / schema_name
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(
            dict(payload)
        ),
        key=lambda error: tuple(
            str(item)
            for item in error.absolute_path
        ),
    )
    if errors:
        first = errors[0]
        path = "$" + "".join(
            (
                f"[{item}]"
                if isinstance(item, int)
                else f".{item}"
            )
            for item in first.absolute_path
        )
        raise StateValidationError(
            f"Stage F文档不符合{schema_name}："
            f"{path}: {first.message}",
            code="STAGE_F_SCHEMA_INVALID",
            details={
                "schema": schema_name,
                "path": path,
            },
        )


def _order_action_document(
    plan: ProposedOrderPlan,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "profile_id": plan.profile_id,
        "strategy_id": plan.strategy_id,
        "strategy_version": plan.strategy_version,
        "run_date": plan.run_date,
        "cycle_id": plan.cycle_id,
        "generated_at": plan.generated_at,
        "submission_requested": (
            plan.permission.submission_requested
        ),
        "submission_performed": False,
        "actions": [
            action.to_dict()
            for action in plan.actions
        ],
    }


def _validation_summary_document(
    plan: ValidatedOrderPlan,
) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload["summary"])
    return {
        "schema_version": "1.0",
        "profile_id": plan.proposed.profile_id,
        "strategy_id": plan.proposed.strategy_id,
        "strategy_version": (
            plan.proposed.strategy_version
        ),
        "run_date": plan.proposed.run_date,
        "cycle_id": plan.proposed.cycle_id,
        "generated_at": plan.generated_at,
        "hard_validation_passed": (
            not plan.global_issues
            and summary.get("blocked", 0) == 0
        ),
        "global_issue_count": len(
            plan.global_issues
        ),
        "global_issues": [
            issue.to_dict()
            for issue in plan.global_issues
        ],
        "summary": summary,
        "submission_requested": (
            plan.proposed.permission
            .submission_requested
        ),
        "submission_performed": False,
        "submitted_order_count": 0,
    }


def _account_binding_hash(
    profile_id: str,
    *,
    project_root: Path | None,
) -> str | None:
    path = account_binding_path(
        profile_id,
        project_root=project_root,
    )
    if not path.is_file():
        return None
    value = load_json_object(path).get(
        "account_id_hash"
    )
    return (
        str(value)
        if isinstance(value, str) and value
        else None
    )


def run_stage_f(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
    coarse_runner: CoarseRunner | None = None,
    portfolio_runner: PortfolioRunner | None = None,
    execution_runner: ExecutionRunner | None = None,
    bar_store: DailyBarStore | None = None,
    review_input_func: Callable[[str], str] = input,
    review_stdin: TextIO | None = None,
) -> StageFRunResult:
    """Build and validate local orders, then pause before all broker writes."""

    stage_e_result = run_stage_e(
        options,
        project_root=project_root,
        clients=clients,
        coarse_runner=coarse_runner,
        portfolio_runner=portfolio_runner,
        execution_runner=execution_runner,
        bar_store=bar_store,
        review_input_func=review_input_func,
        review_stdin=review_stdin,
    )
    resolution = stage_e_result.resolution
    paths = resolution.paths
    state = resolution.state
    snapshot_result: PreTradeSnapshotResult | None = None
    proposed: ProposedOrderPlan | None = None
    validated: ValidatedOrderPlan | None = None
    request_specs: tuple[BrokerRequestSpec, ...] = ()
    validated_document: Mapping[str, Any] | None = None

    profile = load_profile(
        paths.profile_id,
        project_root=project_root,
    )
    if profile.order_policy is None:
        raise ConfigurationError(
            "Stage F profile缺少order_policy",
            code="ORDER_POLICY_REFERENCE_MISSING",
        )
    risk_profile = load_risk_profile(
        profile.risk_profile,
        project_root=project_root,
    )
    order_policy = load_order_policy(
        profile.order_policy,
        project_root=project_root,
    )
    execution_output = load_json_object(
        paths.execution_output
    )
    portfolio_output = load_json_object(
        paths.portfolio_output
    )

    if next_step(state) == StepName.BUILD_ORDERS:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        try:
            active_clients = clients
            if active_clients is None:
                active_clients = create_alpaca_clients(
                    paper=options.paper,
                    live=options.live,
                    project_root=project_root,
                    profile=profile,
                )
            snapshot_result = (
                create_pretrade_snapshot(
                    paths,
                    active_clients,
                    execution_output=(
                        execution_output
                    ),
                    order_policy=order_policy,
                )
            )
            _validate_stage_f_document(
                snapshot_result.payload,
                schema_name=(
                    "pretrade_snapshot.schema.json"
                ),
                project_root=project_root,
            )
            proposed = build_order_plan(
                paths=paths,
                state=state,
                execution_output=execution_output,
                pretrade_snapshot=(
                    snapshot_result.snapshot
                ),
                portfolio_output=portfolio_output,
                risk_profile=risk_profile,
                order_policy=order_policy,
            )
            proposed_document = proposed.to_dict()
            _validate_stage_f_document(
                proposed_document,
                schema_name=(
                    "proposed_orders.schema.json"
                ),
                project_root=project_root,
            )
            atomic_write_json(
                paths.proposed_orders,
                proposed_document,
            )
            atomic_write_json(
                paths.order_action_plan,
                _order_action_document(proposed),
            )
            complete_current_step(
                state,
                output_path=str(
                    paths.proposed_orders
                ),
                message=(
                    "完成pre-trade刷新与确定性订单构建"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
        except Exception as error:
            fail_current_step(state, error)
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            raise

    if next_step(state) == StepName.VALIDATE_ORDERS:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        try:
            if proposed is None:
                proposed = (
                    ProposedOrderPlan.from_dict(
                        load_json_object(
                            paths.proposed_orders
                        )
                    )
                )
            pretrade_payload = (
                dict(snapshot_result.payload)
                if snapshot_result is not None
                else load_json_object(
                    paths.pretrade_snapshot
                )
            )
            validated = validate_order_plan(
                plan=proposed,
                execution_output=execution_output,
                pretrade_snapshot=pretrade_payload,
                risk_profile=risk_profile,
                order_policy=order_policy,
                expected_account_id_hash=(
                    _account_binding_hash(
                        paths.profile_id,
                        project_root=project_root,
                    )
                ),
            )
            validated_document = (
                validated.to_dict()
            )
            _validate_stage_f_document(
                validated_document,
                schema_name=(
                    "validated_orders.schema.json"
                ),
                project_root=project_root,
            )
            request_specs = create_request_specs(
                validated
            )
            atomic_write_json(
                paths.validated_orders,
                dict(validated_document),
            )
            atomic_write_json(
                paths.order_request_specs,
                request_specs_document(
                    validated,
                    request_specs,
                ),
            )
            atomic_write_json(
                paths.order_validation_summary,
                _validation_summary_document(
                    validated
                ),
            )
            complete_current_step(
                state,
                output_path=str(
                    paths.validated_orders
                ),
                message=(
                    "完成Python订单硬校验与本地SDK请求建模"
                ),
            )
            save_cycle_state(
                paths.cycle_state,
                state,
            )
        except Exception as error:
            fail_current_step(state, error)
            save_cycle_state(
                paths.cycle_state,
                state,
            )
            raise

    if next_step(state) == StepName.SUBMIT_ORDERS:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)

    if (
        validated_document is None
        and paths.validated_orders.is_file()
    ):
        validated_document = load_json_object(
            paths.validated_orders
        )
    return StageFRunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        stage_e_result=stage_e_result,
        pretrade_snapshot=snapshot_result,
        proposed=proposed,
        validated=validated,
        request_specs=request_specs,
        validated_document=validated_document,
        stopped_at=next_step(state),
    )


def print_stage_f_result(
    result: StageFRunResult,
) -> None:
    """Print only planning facts and the explicit zero-submission boundary."""

    state = result.resolution.state
    print_bootstrap_result(result.resolution)
    print(f"Profile：{state.profile_id}")
    print(
        "策略："
        f"{state.release['strategy_id']}@"
        f"{state.release['strategy_version']}"
    )
    print(f"风险：{state.release['risk_profile']}")
    print(
        f"订单策略：{state.release['order_policy']}"
    )
    print(
        "交易提交权限："
        + (
            "enabled"
            if state.trade_permission
            .submission_enabled
            else "dry-run"
        )
    )
    print(
        "Pre-trade刷新："
        + (
            "成功"
            if (
                result.pretrade_snapshot is None
                or result.pretrade_snapshot
                .order_planning_ready
            )
            else "关键数据失败，已全局阻止"
        )
    )
    document = result.validated_document or {}
    summary = document.get("summary", {})
    summary = (
        summary
        if isinstance(summary, Mapping)
        else {}
    )
    print(
        f"拟定订单：{summary.get('proposed', 0)}"
    )
    print(
        f"批准订单：{summary.get('approved', 0)}"
    )
    print(
        "Dry-run批准："
        f"{summary.get('dry_run_approved', 0)}"
    )
    print(
        f"阻止订单：{summary.get('blocked', 0)}"
    )
    print(
        f"跳过订单：{summary.get('skipped', 0)}"
    )
    print(
        f"依赖订单：{summary.get('dependent', 0)}"
    )
    print(
        "预计买入资金："
        f"{summary.get('estimated_buy_value', '0')}"
    )
    print(
        "预计卖出金额："
        f"{summary.get('estimated_sell_value', '0')}"
    )
    print(
        "下一步骤："
        f"{result.stopped_at.value if result.stopped_at else '无'}"
    )
    print("订单提交阶段尚未实现")
    print("实际提交订单数：0")
    print("未调用提交、取消、替换或平仓接口")


def _submission_operations(
    *,
    validated: Mapping[str, Any],
    request_specs: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    allow_trade: bool,
) -> list[SubmissionOperation]:
    """Prepare a stable sequential operation list without broker access."""

    status_by_plan = {
        str(item.get("plan_id")): str(
            item.get("status", "")
        )
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
        and item.get("plan_id")
    }
    operations: list[SubmissionOperation] = []
    for item in action_plan.get("actions", []):
        if (
            not isinstance(item, Mapping)
            or item.get("action")
            not in {"cancel", "replace"}
        ):
            continue
        action_id = str(item.get("action_id", ""))
        if not action_id:
            continue
        permitted = (
            allow_trade
            and item.get("status")
            in {"approved", "dependent"}
        )
        operations.append(
            SubmissionOperation(
                operation_id=f"cancel-{action_id}",
                operation_type=(
                    SubmissionOperationType.CANCEL
                ),
                plan_id=None,
                client_order_id=(
                    str(item["client_order_id"])
                    if item.get("client_order_id")
                    else None
                ),
                broker_order_id=(
                    str(item["broker_order_id"])
                    if item.get("broker_order_id")
                    else None
                ),
                symbol=str(item.get("symbol", "")),
                state=(
                    SubmissionOperationState.PREPARED
                    if permitted
                    else SubmissionOperationState.SKIPPED
                ),
                request_summary={
                    "action": "cancel",
                    "source_intent": item.get("action"),
                    "broker_order_id": item.get(
                        "broker_order_id"
                    ),
                },
            )
        )
    for spec in request_specs.get("requests", []):
        if not isinstance(spec, Mapping):
            continue
        plan_id = str(spec.get("plan_id", ""))
        status = status_by_plan.get(plan_id, "")
        permitted = allow_trade and status == "approved"
        operations.append(
            SubmissionOperation(
                operation_id=f"submit-{plan_id}",
                operation_type=(
                    SubmissionOperationType.SUBMIT
                ),
                plan_id=plan_id,
                client_order_id=str(
                    spec.get("client_order_id", "")
                ),
                broker_order_id=None,
                symbol=str(spec.get("symbol", "")),
                state=(
                    SubmissionOperationState.PREPARED
                    if permitted
                    else SubmissionOperationState.SKIPPED
                ),
                request_summary={
                    key: spec.get(key)
                    for key in (
                        "request_class",
                        "symbol",
                        "qty",
                        "side",
                        "time_in_force",
                        "limit_price",
                        "order_class",
                        "stop_price",
                        "trail_price",
                        "trail_percent",
                        "take_profit_limit_price",
                        "stop_loss_stop_price",
                        "stop_loss_limit_price",
                        "protection_role",
                        "extended_hours",
                        "client_order_id",
                    )
                },
            )
        )
    return operations


def _submission_intent(
    *,
    state: CycleState,
    policy: Any,
    validated: Mapping[str, Any],
    request_specs: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    operations: list[SubmissionOperation],
) -> SubmissionIntent:
    approved = tuple(
        str(item["plan_id"])
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
        and item.get("status") == "approved"
    )
    dependent = tuple(
        str(item["plan_id"])
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
        and item.get("status") == "dependent"
    )
    cancels = tuple(
        str(item["action_id"])
        for item in action_plan.get("actions", [])
        if isinstance(item, Mapping)
        and item.get("action") in {"cancel", "replace"}
        and item.get("status")
        in {"approved", "dependent"}
    )
    expected = sum(
        operation.state
        == SubmissionOperationState.PREPARED
        or operation.attempt_count > 0
        for operation in operations
    )
    return SubmissionIntent(
        profile_id=state.profile_id,
        environment=policy.environment,
        run_date=state.run_date,
        cycle_id=state.cycle_id,
        allow_trade=state.invocation.allow_trade,
        validated_orders_hash=canonical_hash(
            validated
        ),
        request_specs_hash=canonical_hash(
            request_specs
        ),
        action_plan_hash=canonical_hash(
            action_plan
        ),
        submission_policy=policy.reference,
        submission_policy_hash=sha256_file(
            policy.source_path
        ),
        approved_plan_ids=approved,
        dependent_plan_ids=dependent,
        cancel_action_ids=cancels,
        expected_write_count=expected,
    )


def _revise_submission_intent(
    existing: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    """Append the prior hashes before activating a post-cancel intent."""

    required_history_fields = (
        "intent_revision",
        "created_at",
        "validated_orders_hash",
        "request_specs_hash",
        "action_plan_hash",
        "approved_plan_ids",
        "dependent_plan_ids",
        "cancel_action_ids",
        "expected_write_count",
    )
    if any(field not in existing for field in required_history_fields):
        raise SafetyBlockedError(
            "原submission intent缺少修订所需字段",
            code="SUBMISSION_INTENT_REVISION_BLOCKED",
        )
    previous = {
        field: existing[field]
        for field in required_history_fields
    }
    previous["superseded_at"] = utc_now_iso()
    prior = existing.get("prior_revisions", [])
    if not isinstance(prior, list):
        raise SafetyBlockedError(
            "submission intent修订历史格式无效",
            code="SUBMISSION_INTENT_REVISION_BLOCKED",
        )
    revised = dict(replacement)
    revised["created_at"] = existing["created_at"]
    revised["intent_revision"] = (
        int(existing["intent_revision"]) + 1
    )
    revised["prior_revisions"] = [
        *[dict(item) for item in prior if isinstance(item, Mapping)],
        previous,
    ]
    return revised


def _refresh_after_confirmed_replacements(
    *,
    paths: CyclePaths,
    state: CycleState,
    clients: AlpacaClients,
    project_root: Path,
    profile: Profile,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Refresh broker facts and rebuild all order artifacts after cancel."""

    if profile.order_policy is None:
        raise ConfigurationError(
            "replacement刷新缺少order_policy",
            code="ORDER_POLICY_REFERENCE_MISSING",
        )
    execution_output = load_json_object(
        paths.execution_output
    )
    portfolio_output = load_json_object(
        paths.portfolio_output
    )
    risk_profile = load_risk_profile(
        profile.risk_profile,
        project_root=project_root,
    )
    order_policy = load_order_policy(
        profile.order_policy,
        project_root=project_root,
    )
    refreshed = create_pretrade_snapshot(
        paths,
        clients,
        execution_output=execution_output,
        order_policy=order_policy,
    )
    _validate_stage_f_document(
        refreshed.payload,
        schema_name="pretrade_snapshot.schema.json",
        project_root=project_root,
    )
    proposed = build_order_plan(
        paths=paths,
        state=state,
        execution_output=execution_output,
        pretrade_snapshot=refreshed.snapshot,
        portfolio_output=portfolio_output,
        risk_profile=risk_profile,
        order_policy=order_policy,
    )
    proposed_document = proposed.to_dict()
    action_document = _order_action_document(proposed)
    validated_plan = validate_order_plan(
        plan=proposed,
        execution_output=execution_output,
        pretrade_snapshot=refreshed.payload,
        risk_profile=risk_profile,
        order_policy=order_policy,
        expected_account_id_hash=_account_binding_hash(
            paths.profile_id,
            project_root=project_root,
        ),
    )
    validated_document = validated_plan.to_dict()
    specifications = create_request_specs(
        validated_plan
    )
    request_document = request_specs_document(
        validated_plan,
        specifications,
    )
    for document, schema_name in (
        (
            proposed_document,
            "proposed_orders.schema.json",
        ),
        (
            validated_document,
            "validated_orders.schema.json",
        ),
    ):
        _validate_stage_f_document(
            document,
            schema_name=schema_name,
            project_root=project_root,
        )
    atomic_write_json(
        paths.proposed_orders,
        proposed_document,
    )
    atomic_write_json(
        paths.order_action_plan,
        action_document,
    )
    atomic_write_json(
        paths.validated_orders,
        validated_document,
    )
    atomic_write_json(
        paths.order_request_specs,
        request_document,
    )
    return (
        validated_document,
        request_document,
        action_document,
    )


def _cycle_final_status(
    *,
    allow_trade: bool,
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
) -> CycleStatus:
    summary = reconciliation.get("summary", {})
    summary = (
        summary
        if isinstance(summary, Mapping)
        else {}
    )
    if not allow_trade:
        return CycleStatus.COMPLETED_DRY_RUN
    if (
        int(submission.get("uncertain_count", 0)) > 0
        or int(summary.get("uncertain", 0)) > 0
    ):
        return CycleStatus.BLOCKED_SUBMISSION_UNCERTAIN
    if int(summary.get("partially_filled", 0)) > 0:
        return CycleStatus.COMPLETED_WITH_PARTIAL_FILLS
    if (
        int(summary.get("rejected", 0)) > 0
        or int(submission.get("rejected_count", 0)) > 0
    ):
        return CycleStatus.COMPLETED_WITH_REJECTIONS
    if int(summary.get("open", 0)) > 0:
        return CycleStatus.COMPLETED_WITH_OPEN_ORDERS
    if (
        int(submission.get("submitted_count", 0)) > 0
        or int(
            submission.get(
                "cancel_requested_count", 0
            )
        )
        > 0
    ):
        return CycleStatus.COMPLETED_WITH_SUBMISSIONS
    return CycleStatus.COMPLETED_NO_ACTION


def _assert_fresh_order_capacity(
    *,
    spec: Mapping[str, Any],
    validated_order: Mapping[str, Any],
    account: Mapping[str, Any],
    positions: list[Mapping[str, Any]],
) -> None:
    """Recheck sequential buying power or sell availability immediately."""

    side = str(spec.get("side", "")).lower()
    qty = Decimal(str(spec.get("qty", "0")))
    if qty <= 0:
        raise ValueError("fresh order quantity必须大于0")
    if side == "buy":
        planned = Decimal(
            str(validated_order.get("planned_value", "0"))
        )
        buying_power = Decimal(
            str(account.get("buying_power", "0"))
        )
        if planned <= 0 or planned > buying_power:
            raise ValueError(
                "最新buying power不足以覆盖已校验订单"
            )
        return
    if side == "sell":
        symbol = str(spec.get("symbol", "")).upper()
        available = sum(
            Decimal(
                str(
                    position.get(
                        "available_quantity", "0"
                    )
                )
            )
            for position in positions
            if str(
                position.get("symbol", "")
            ).upper()
            == symbol
        )
        if qty > available:
            raise ValueError(
                "最新available quantity不足以覆盖卖单"
            )
        return
    raise ValueError("fresh order side无效")


def _cycle_summary_document(
    *,
    state: CycleState,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    final_status: CycleStatus,
) -> dict[str, Any]:
    validated_summary = validated.get("summary", {})
    validated_summary = (
        validated_summary
        if isinstance(validated_summary, Mapping)
        else {}
    )
    reconcile_summary = reconciliation.get("summary", {})
    reconcile_summary = (
        reconcile_summary
        if isinstance(reconcile_summary, Mapping)
        else {}
    )
    return {
        "schema_version": "1.0",
        "profile_id": state.profile_id,
        "strategy_id": state.release["strategy_id"],
        "strategy_version": state.release[
            "strategy_version"
        ],
        "run_date": state.run_date,
        "cycle_id": state.cycle_id,
        "cycle_kind": state.cycle_kind.value,
        "started_at": state.created_at,
        "completed_at": utc_now_iso(),
        "final_status": final_status.value,
        "coarse": {
            "output_path": (
                str(state.stages["coarse"].output_path)
                if state.stages["coarse"].output_path
                else None
            )
        },
        "portfolio": {
            "output_path": (
                str(
                    state.stages[
                        "portfolio"
                    ].output_path
                )
                if state.stages[
                    "portfolio"
                ].output_path
                else None
            )
        },
        "execution": {
            "output_path": (
                str(
                    state.stages[
                        "execution"
                    ].output_path
                )
                if state.stages[
                    "execution"
                ].output_path
                else None
            )
        },
        "orders": {
            "proposed": int(
                validated_summary.get("proposed", 0)
            ),
            "approved": int(
                validated_summary.get("approved", 0)
            ),
            "submitted": int(
                submission.get("submitted_count", 0)
            ),
            "filled": int(
                reconcile_summary.get("filled", 0)
            ),
            "partially_filled": int(
                reconcile_summary.get(
                    "partially_filled", 0
                )
            ),
            "open": int(
                reconcile_summary.get("open", 0)
            ),
            "rejected": int(
                reconcile_summary.get("rejected", 0)
            ),
        },
        "capital": dict(
            reconciliation.get("capital", {})
        ),
        "warnings": [
            warning.to_dict()
            for warning in state.warnings
        ]
        + list(reconciliation.get("warnings", [])),
        "errors": [
            error.to_dict() for error in state.errors
        ]
        + list(reconciliation.get("errors", [])),
    }


def _load_coarse_report_context(
    paths: CyclePaths,
) -> dict[str, Any]:
    """Resolve the installed coarse output without trusting an external path."""

    if not paths.coarse_current.is_file():
        return {}
    index = load_json_object(paths.coarse_current)
    raw_path = index.get("output_path")
    if not isinstance(raw_path, str):
        return {}
    output_path = Path(raw_path).expanduser().resolve()
    if not output_path.is_file():
        return {}
    try:
        output_path.relative_to(
            paths.coarse_directory.resolve()
        )
    except ValueError:
        return {}
    return load_json_object(output_path)


def _load_optional_object(path: Path) -> dict[str, Any]:
    return load_json_object(path) if path.is_file() else {}


def _maintain_natural_report(
    *,
    paths: CyclePaths,
    state: CycleState,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Maintain the Live narrative without changing broker outcomes on failure."""

    if not state.invocation.live:
        return
    error_path = natural_report_error_path(
        paths.daily_report
    )
    try:
        result = update_natural_language_report(
            paths.daily_report,
            state=state,
            validated=validated,
            submission=submission,
            reconciliation=reconciliation,
            context=context,
        )
        error_path.unlink(missing_ok=True)
        print(
            "自然语言日报："
            f"{result.status} / {result.path}",
            flush=True,
        )
    except Exception as error:
        atomic_write_json(
            error_path,
            {
                "schema_version": "1.0",
                "profile_id": state.profile_id,
                "run_date": state.run_date,
                "cycle_id": state.cycle_id,
                "code": "NATURAL_REPORT_FAILED",
                "exception_type": (
                    error.__class__.__name__
                ),
                "recorded_at": utc_now_iso(),
            },
        )
        fallback = (
            write_fallback_natural_language_report(
                paths.daily_report,
                state=state,
                validated=validated,
                submission=submission,
                reconciliation=reconciliation,
                context=context,
            )
        )
        print(
            "自然语言日报生成失败；"
            "已写入无新闻的事实降级版："
            f"{fallback.path}；错误：{error_path}",
            flush=True,
        )


def _run_stage_g_maintenance_only(
    options: CLIOptions,
    *,
    root: Path,
    clients: AlpacaClients,
    profile: Profile,
    policy: Any,
    maintained: list[str],
) -> StageGRunResult:
    """Persist a read-only maintenance cycle without requiring decision artifacts."""

    stage_b = run_stage_b(
        options,
        project_root=root,
        clients=clients,
    )
    resolution = stage_b.resolution
    paths = resolution.paths
    state = resolution.state
    validated: dict[str, Any] = {
        "profile_id": state.profile_id,
        "cycle_id": state.cycle_id,
        "submission_requested": False,
        "submission_performed": False,
        "orders": [],
        "summary": {
            "proposed": 0,
            "approved": 0,
            "dry_run_approved": 0,
            "blocked": 0,
            "skipped": 0,
            "dependent": 0,
        },
    }
    request_specs = {
        "submission_requested": False,
        "submission_performed": False,
        "requests": [],
    }
    action_plan = {
        "submission_requested": False,
        "submission_performed": False,
        "actions": [],
    }
    intent_document = SubmissionIntent(
        profile_id=state.profile_id,
        environment=profile.environment,
        run_date=state.run_date,
        cycle_id=state.cycle_id,
        allow_trade=False,
        validated_orders_hash=canonical_hash(validated),
        request_specs_hash=canonical_hash(request_specs),
        action_plan_hash=canonical_hash(action_plan),
        submission_policy=policy.reference,
        submission_policy_hash=sha256_file(
            policy.source_path
        ),
        approved_plan_ids=(),
        dependent_plan_ids=(),
        cancel_action_ids=(),
        expected_write_count=0,
    ).to_dict()
    atomic_write_json(
        paths.submission_intent, intent_document
    )
    journal = SubmissionJournal.load_or_create(
        paths.submission_journal,
        profile_id=state.profile_id,
        environment=profile.environment,
        run_date=state.run_date,
        cycle_id=state.cycle_id,
        operations=[],
    )
    submission_document = broker_submission_document(
        profile_id=state.profile_id,
        environment=profile.environment,
        run_date=state.run_date,
        cycle_id=state.cycle_id,
        submission_requested=False,
        submission_performed=False,
        validated_orders_hash=canonical_hash(validated),
        operations=journal.operations,
        started_at=utc_now_iso(),
        global_warnings=[
            (
                "maintenance_only reconciled prior cycles: "
                + (
                    ",".join(maintained)
                    if maintained
                    else "none"
                )
            )
        ],
    )
    atomic_write_json(
        paths.broker_submission, submission_document
    )
    reconciliation_document = reconcile_submission(
        clients=clients,
        profile_id=state.profile_id,
        environment=profile.environment,
        cycle_id=state.cycle_id,
        operations=[],
        output_path=paths.reconciliation,
    )
    final_status = CycleStatus.COMPLETED_NO_ACTION
    cycle_summary = _cycle_summary_document(
        state=state,
        validated=validated,
        submission=submission_document,
        reconciliation=reconciliation_document,
        final_status=final_status,
    )
    if next_step(state) == StepName.SAVE_CYCLE:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        atomic_write_json(
            paths.cycle_summary, cycle_summary
        )
        complete_current_step(
            state,
            output_path=str(paths.cycle_summary),
            message="保存maintenance cycle summary",
        )
        save_cycle_state(paths.cycle_state, state)
    if next_step(state) == StepName.UPDATE_REPORT:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        report_context = {
            "initial_guidance": _load_optional_object(
                paths.initial_guidance
            )
        }
        update_daily_report(
            paths.daily_report,
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            context=report_context,
        )
        _maintain_natural_report(
            paths=paths,
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            context=report_context,
        )
        complete_current_step(
            state,
            output_path=str(paths.daily_report),
            message="追加paper旧订单维护日报",
        )
        save_cycle_state(paths.cycle_state, state)
    if next_step(state) == StepName.COMPLETE:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        complete_current_step(
            state,
            final_status=final_status,
            message=final_status.value,
        )
        save_cycle_state(paths.cycle_state, state)
        daily_state = load_daily_state(paths.daily_state)
        daily_state.detailed_report_created = (
            paths.daily_report.is_file()
        )
        mark_daily_cycle_terminal(daily_state, state)
        save_daily_state(paths.daily_state, daily_state)
        cycle_summary = _cycle_summary_document(
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            final_status=final_status,
        )
        atomic_write_json(
            paths.cycle_summary, cycle_summary
        )
    return StageGRunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        stage_f_result=None,
        submission_intent=intent_document,
        broker_submission=submission_document,
        reconciliation=reconciliation_document,
        cycle_summary=cycle_summary,
        maintained_cycle_ids=tuple(maintained),
        stopped_at=next_step(state),
    )


def run_stage_g(
    options: CLIOptions,
    *,
    project_root: Path | None = None,
    clients: AlpacaClients | None = None,
    coarse_runner: CoarseRunner | None = None,
    portfolio_runner: PortfolioRunner | None = None,
    execution_runner: ExecutionRunner | None = None,
    bar_store: DailyBarStore | None = None,
    review_input_func: Callable[[str], str] = input,
    review_stdin: TextIO | None = None,
) -> StageGRunResult:
    """Run the complete Paper/Live cycle while keeping writes fail closed."""

    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    profile = load_profile(
        options.profile,
        project_root=root,
    )
    if profile.submission_policy is None:
        raise ConfigurationError(
            "当前profile缺少submission_policy",
            code="SUBMISSION_POLICY_REFERENCE_MISSING",
        )
    policy = load_submission_policy(
        profile.submission_policy,
        project_root=root,
    )
    active_clients = clients or create_alpaca_clients(
        paper=options.paper,
        live=options.live,
        project_root=root,
        profile=profile,
    )
    raw_account = call_api(
        "get_account_for_stage_g_identity",
        active_clients.trading.get_account,
    )
    raw_account_id = (
        raw_account.get("id")
        if isinstance(raw_account, Mapping)
        else getattr(raw_account, "id", None)
    )
    verify_or_bind_account(
        profile,
        raw_account_id,
        bind_account=options.bind_account,
        project_root=root,
    )
    maintained = maintain_previous_submissions(
        clients=active_clients,
        project_root=root,
        run_date=normalize_run_date(options.run_date),
        profile_id=profile.profile_id,
        strategy_id=profile.strategy_id,
        strategy_version=profile.strategy_version,
    )
    if options.maintenance_only:
        return _run_stage_g_maintenance_only(
            options,
            root=root,
            clients=active_clients,
            profile=profile,
            policy=policy,
            maintained=maintained,
        )
    stage_f_result = run_stage_f(
        options,
        project_root=root,
        clients=active_clients,
        coarse_runner=coarse_runner,
        portfolio_runner=portfolio_runner,
        execution_runner=execution_runner,
        bar_store=bar_store,
        review_input_func=review_input_func,
        review_stdin=review_stdin,
    )
    resolution = stage_f_result.resolution
    paths = resolution.paths
    state = resolution.state
    validated = load_json_object(
        paths.validated_orders
    )
    request_specs = load_json_object(
        paths.order_request_specs
    )
    action_plan = load_json_object(
        paths.order_action_plan
    )

    if paths.submission_intent.is_file():
        intent_document = load_json_object(
            paths.submission_intent
        )
        expected_intent = _submission_intent(
            state=state,
            policy=policy,
            validated=validated,
            request_specs=request_specs,
            action_plan=action_plan,
            operations=[],
        ).to_dict()
        for field in (
            "profile_id",
            "cycle_id",
            "allow_trade",
            "validated_orders_hash",
            "request_specs_hash",
            "action_plan_hash",
            "submission_policy",
            "submission_policy_hash",
        ):
            if (
                intent_document.get(field)
                != expected_intent.get(field)
            ):
                raise SafetyBlockedError(
                    "现有submission intent与当前产物不一致",
                    code="SUBMISSION_INTENT_MISMATCH",
                    details={"field": field},
                )
        initial_operations = (
            []
            if paths.submission_journal.is_file()
            else _submission_operations(
                validated=validated,
                request_specs=request_specs,
                action_plan=action_plan,
                allow_trade=options.allow_trade,
            )
        )
    else:
        initial_operations = _submission_operations(
            validated=validated,
            request_specs=request_specs,
            action_plan=action_plan,
            allow_trade=options.allow_trade,
        )
        intent_document = _submission_intent(
            state=state,
            policy=policy,
            validated=validated,
            request_specs=request_specs,
            action_plan=action_plan,
            operations=initial_operations,
        ).to_dict()
        _validate_stage_f_document(
            intent_document,
            schema_name="submission_intent.schema.json",
            project_root=root,
        )
        atomic_write_json(
            paths.submission_intent,
            intent_document,
        )
    journal = SubmissionJournal.load_or_create(
        paths.submission_journal,
        profile_id=state.profile_id,
        environment=profile.environment,
        run_date=state.run_date,
        cycle_id=state.cycle_id,
        operations=initial_operations,
    )

    started_at = utc_now_iso()
    if paths.broker_submission.is_file():
        submission_document = load_json_object(
            paths.broker_submission
        )
    else:
        if options.allow_trade and journal.operations:
            poll = policy.settings.get(
                "status_poll", {}
            )
            switches = policy.settings.get(
                "deployment_switches", {}
            )
            for operation in journal.operations:
                if (
                    operation.state
                    != SubmissionOperationState.PREPARED
                    or operation.operation_type
                    != SubmissionOperationType.CANCEL
                ):
                    continue
                account = fetch_account(active_clients)
                assert_submission_allowed(
                    profile=profile,
                    policy=policy,
                    state=state,
                    clients=active_clients,
                    account=account,
                    expected_account_hash=(
                        _account_binding_hash(
                            state.profile_id,
                            project_root=root,
                        )
                    ),
                    validated=validated,
                    request_specs=request_specs,
                    action_plan=action_plan,
                    intent=intent_document,
                    journal=journal,
                    broker_submission_exists=False,
                )
                if (
                    policy.settings.get("allow_cancel")
                    is not True
                    or not isinstance(switches, Mapping)
                    or switches.get("cancel_enabled")
                    is not True
                ):
                    journal.transition(
                        operation.operation_id,
                        SubmissionOperationState.FAILED_DEFINITE,
                        error={
                            "type": "CancelDisabled",
                            "message": "submission policy未授权取消",
                        },
                    )
                    continue
                execute_cancel(
                    clients=active_clients,
                    operation=operation,
                    journal=journal,
                    maximum_seconds=float(
                        poll.get("maximum_seconds", 10)
                    ),
                    interval_seconds=float(
                        poll.get("interval_seconds", 1)
                    ),
                )
                if journal.has_uncertain:
                    break

            if not journal.has_uncertain:
                replacement_cancels = [
                    operation
                    for operation in journal.operations
                    if (
                        operation.operation_type
                        == SubmissionOperationType.CANCEL
                        and operation.request_summary.get(
                            "source_intent"
                        )
                        == "replace"
                    )
                ]
                if (
                    replacement_cancels
                    and all(
                        replacement_is_unlocked(
                            operation
                        )
                        for operation
                        in replacement_cancels
                    )
                ):
                    (
                        validated,
                        request_specs,
                        action_plan,
                    ) = _refresh_after_confirmed_replacements(
                        paths=paths,
                        state=state,
                        clients=active_clients,
                        project_root=root,
                        profile=profile,
                    )
                    refreshed_operations = (
                        _submission_operations(
                            validated=validated,
                            request_specs=request_specs,
                            action_plan=action_plan,
                            allow_trade=True,
                        )
                    )
                    journal.replace_unstarted_submissions(
                        [
                            operation
                            for operation
                            in refreshed_operations
                            if operation.operation_type
                            == SubmissionOperationType.SUBMIT
                        ]
                    )
                    replacement_intent = (
                        _submission_intent(
                            state=state,
                            policy=policy,
                            validated=validated,
                            request_specs=request_specs,
                            action_plan=action_plan,
                            operations=journal.operations,
                        ).to_dict()
                    )
                    intent_document = (
                        _revise_submission_intent(
                            intent_document,
                            replacement_intent,
                        )
                    )
                    _validate_stage_f_document(
                        intent_document,
                        schema_name=(
                            "submission_intent.schema.json"
                        ),
                        project_root=root,
                    )
                    atomic_write_json(
                        paths.submission_intent,
                        intent_document,
                    )
                approved_specs = request_specs_for_approved(
                    validated, request_specs
                )
                specs_by_plan = {
                    str(item["plan_id"]): item
                    for item in approved_specs
                }
                validated_by_plan = {
                    str(item["plan_id"]): item
                    for item in validated.get(
                        "orders", []
                    )
                    if isinstance(item, Mapping)
                    and item.get("plan_id")
                }
                for operation in journal.operations:
                    if (
                        operation.state
                        != SubmissionOperationState.PREPARED
                        or operation.operation_type
                        != SubmissionOperationType.SUBMIT
                    ):
                        continue
                    spec = specs_by_plan.get(
                        str(operation.plan_id)
                    )
                    if spec is None:
                        journal.transition(
                            operation.operation_id,
                            SubmissionOperationState.FAILED_DEFINITE,
                            error={
                                "type": "ApprovedSpecMissing",
                                "message": "approved订单缺少request spec",
                            },
                        )
                        continue
                    account = fetch_account(active_clients)
                    positions = fetch_positions(
                        active_clients
                    )
                    assert_submission_allowed(
                        profile=profile,
                        policy=policy,
                        state=state,
                        clients=active_clients,
                        account=account,
                        expected_account_hash=(
                            _account_binding_hash(
                                state.profile_id,
                                project_root=root,
                            )
                        ),
                        validated=validated,
                        request_specs=request_specs,
                        action_plan=action_plan,
                        intent=intent_document,
                        journal=journal,
                        broker_submission_exists=False,
                    )
                    def fresh_write_check(
                        current_spec: Mapping[str, Any] = spec,
                        current_validated: Mapping[str, Any] = (
                            validated_by_plan[
                                str(operation.plan_id)
                            ]
                        ),
                        current_account: Mapping[str, Any] = account,
                        current_positions: list[
                            Mapping[str, Any]
                        ] = positions,
                    ) -> None:
                        _assert_fresh_order_capacity(
                            spec=current_spec,
                            validated_order=current_validated,
                            account=current_account,
                            positions=current_positions,
                        )

                    submit_approved_order(
                        clients=active_clients,
                        spec=spec,
                        operation=operation,
                        journal=journal,
                        write_preflight=fresh_write_check,
                    )
                    if journal.has_uncertain:
                        break
        submission_performed = any(
            operation.request_started_at is not None
            for operation in journal.operations
        )
        submission_document = broker_submission_document(
            profile_id=state.profile_id,
            environment=profile.environment,
            run_date=state.run_date,
            cycle_id=state.cycle_id,
            submission_requested=bool(
                validated.get("submission_requested")
            ),
            submission_performed=submission_performed,
            validated_orders_hash=canonical_hash(
                validated
            ),
            operations=journal.operations,
            started_at=started_at,
        )
        _validate_stage_f_document(
            submission_document,
            schema_name="broker_submission.schema.json",
            project_root=root,
        )
        atomic_write_json(
            paths.broker_submission,
            submission_document,
        )

    reconciliation_document = reconcile_submission(
        clients=active_clients,
        profile_id=state.profile_id,
        environment=profile.environment,
        cycle_id=state.cycle_id,
        operations=journal.operations,
        output_path=paths.reconciliation,
    )
    _validate_stage_f_document(
        reconciliation_document,
        schema_name="reconciliation.schema.json",
        project_root=root,
    )
    if next_step(state) == StepName.SUBMIT_ORDERS:
        complete_current_step(
            state,
            output_path=str(paths.broker_submission),
            message="完成paper提交阶段与即时对账",
        )
        save_cycle_state(paths.cycle_state, state)

    final_status = _cycle_final_status(
        allow_trade=options.allow_trade,
        submission=submission_document,
        reconciliation=reconciliation_document,
    )
    cycle_summary = _cycle_summary_document(
        state=state,
        validated=validated,
        submission=submission_document,
        reconciliation=reconciliation_document,
        final_status=final_status,
    )
    if next_step(state) == StepName.SAVE_CYCLE:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        _validate_stage_f_document(
            cycle_summary,
            schema_name="cycle_summary.schema.json",
            project_root=root,
        )
        atomic_write_json(
            paths.cycle_summary,
            cycle_summary,
        )
        complete_current_step(
            state,
            output_path=str(paths.cycle_summary),
            message="保存Stage G cycle summary",
        )
        save_cycle_state(paths.cycle_state, state)

    report_created = False
    if next_step(state) == StepName.UPDATE_REPORT:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        report_context = {
            "initial_guidance": _load_optional_object(
                paths.initial_guidance
            ),
            "base_snapshot": _load_optional_object(
                paths.base_snapshot
            ),
            "coarse": _load_coarse_report_context(
                paths
            ),
            "portfolio": _load_optional_object(
                paths.portfolio_output
            ),
            "execution": _load_optional_object(
                paths.execution_output
            ),
        }
        report_created = update_daily_report(
            paths.daily_report,
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            context=report_context,
        )
        _maintain_natural_report(
            paths=paths,
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            context=report_context,
        )
        complete_current_step(
            state,
            output_path=str(paths.daily_report),
            message="创建或追加Stage G日报",
        )
        save_cycle_state(paths.cycle_state, state)

    if next_step(state) == StepName.COMPLETE:
        begin_next_step(state)
        save_cycle_state(paths.cycle_state, state)
        complete_current_step(
            state,
            message=final_status.value,
            final_status=final_status,
        )
        save_cycle_state(paths.cycle_state, state)
        daily_state = load_daily_state(
            paths.daily_state
        )
        daily_state.detailed_report_created = (
            paths.daily_report.is_file()
        )
        mark_daily_cycle_terminal(
            daily_state, state
        )
        save_daily_state(
            paths.daily_state,
            daily_state,
        )
        cycle_summary = _cycle_summary_document(
            state=state,
            validated=validated,
            submission=submission_document,
            reconciliation=reconciliation_document,
            final_status=final_status,
        )
        atomic_write_json(
            paths.cycle_summary,
            cycle_summary,
        )

    return StageGRunResult(
        resolution=CycleResolution(
            paths=paths,
            state=state,
            resumed=resolution.resumed,
            reason=resolution.reason,
        ),
        stage_f_result=stage_f_result,
        submission_intent=intent_document,
        broker_submission=submission_document,
        reconciliation=reconciliation_document,
        cycle_summary=cycle_summary,
        maintained_cycle_ids=tuple(maintained),
        stopped_at=next_step(state),
    )


def print_stage_g_result(
    result: StageGRunResult,
) -> None:
    """Print persisted broker facts, never planned fills as executions."""

    state = result.resolution.state
    submission = result.broker_submission
    reconciliation = result.reconciliation
    print_bootstrap_result(result.resolution)
    print(
        f"Submission policy："
        f"{state.release['submission_policy']}"
    )
    print(
        "启动维护旧cycle："
        f"{len(result.maintained_cycle_ids)}"
    )
    print(
        "模式："
        + (
            (
                "live-submit"
                if state.invocation.live
                else "paper-submit"
            )
            if state.invocation.allow_trade
            else "dry-run"
        )
    )
    print(
        f"提交：{submission.get('submitted_count', 0)}"
    )
    print(
        f"取消确认：{submission.get('cancel_confirmed_count', 0)}"
    )
    print(
        f"不确定：{submission.get('uncertain_count', 0)}"
    )
    summary = reconciliation.get("summary", {})
    summary = summary if isinstance(summary, Mapping) else {}
    print(f"成交：{summary.get('filled', 0)}")
    print(
        f"部分成交：{summary.get('partially_filled', 0)}"
    )
    print(f"Open：{summary.get('open', 0)}")
    print(f"拒绝：{summary.get('rejected', 0)}")
    print(f"最终状态：{state.status.value}")
    print(f"Cycle summary：{result.resolution.paths.cycle_summary}")
    print(f"日报：{result.resolution.paths.daily_report}")
    natural_report = natural_report_path(
        result.resolution.paths.daily_report
    )
    if natural_report.is_file():
        print(f"自然语言日报：{natural_report}")


def _raise_runtime_interrupted(
    signum: int,
    frame: object,
) -> None:
    """Turn terminal signals into a persistable retryable failure."""

    del frame
    raise TemporaryDataError(
        "运行已被用户或服务安全中断；当前步骤可恢复",
        code="RUN_INTERRUPTED",
        details={"signal": signum},
    )


def main() -> int:
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    signal.signal(
        signal.SIGTERM,
        _raise_runtime_interrupted,
    )
    signal.signal(
        signal.SIGINT,
        _raise_runtime_interrupted,
    )
    print(f"脚本版本：{SCRIPT_VERSION}", flush=True)

    try:
        try:
            options = parse_cli_args()
            result = run_stage_g(
                options
            )
            print_stage_g_result(
                result
            )
            return 0

        except V2Error as error:
            disposition = error.disposition()
            print("v2主流程拒绝或失败")
            print(f"错误代码：{disposition.code}")
            print(f"错误信息：{disposition.message}")
            if (
                disposition.code
                == "ACCOUNT_BINDING_REQUIRED"
                and disposition.details.get(
                    "account_id_hash"
                )
            ):
                print(
                    "待绑定账户hash："
                    f"{disposition.details['account_id_hash']}"
                )
            return 2

        except Exception as error:
            print("v2主流程初始化失败")
            print(f"错误信息：{error}")
            return 1
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)


if __name__ == "__main__":
    raise SystemExit(main())
