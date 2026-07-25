"""Deterministic, resumable step routing for WA Trader v2 Phase A.

This module does not fetch data, invoke Codex, or place orders.  Later phases
provide handlers for the steps defined here.  Phase A owns sequencing,
skip/reuse routing, persisted attempts, and error-to-state classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from v2.exceptions import (
    ErrorCategory,
    StateValidationError,
    classify_exception,
)
from v2.models.state import (
    CoarseStatus,
    CycleKind,
    CycleState,
    CycleStatus,
    DailyState,
    ReviewMode,
    StageName,
    StageStatus,
    StateMessage,
    StepName,
    TERMINAL_CYCLE_STATUSES,
    complete_cycle,
)
from v2.runtime import utc_now_iso


DAILY_FULL_STEPS = (
    StepName.MAINTAIN_PREVIOUS,
    StepName.REFRESH_BASE_DATA,
    StepName.DECIDE_CYCLE_KIND,
    StepName.RUN_COARSE,
    StepName.RUN_PORTFOLIO,
    StepName.COLLECT_REVIEW,
    StepName.REFRESH_EXECUTION_DATA,
    StepName.RUN_EXECUTION,
    StepName.BUILD_ORDERS,
    StepName.VALIDATE_ORDERS,
    StepName.SUBMIT_ORDERS,
    StepName.SAVE_CYCLE,
    StepName.UPDATE_REPORT,
    StepName.COMPLETE,
)

INTRADAY_REBALANCE_STEPS = tuple(
    step
    for step in DAILY_FULL_STEPS
    if step != StepName.RUN_COARSE
)

EXECUTION_REFRESH_STEPS = (
    StepName.MAINTAIN_PREVIOUS,
    StepName.REFRESH_BASE_DATA,
    StepName.DECIDE_CYCLE_KIND,
    StepName.REFRESH_EXECUTION_DATA,
    StepName.RUN_EXECUTION,
    StepName.BUILD_ORDERS,
    StepName.VALIDATE_ORDERS,
    StepName.SUBMIT_ORDERS,
    StepName.SAVE_CYCLE,
    StepName.UPDATE_REPORT,
    StepName.COMPLETE,
)

MAINTENANCE_ONLY_STEPS = (
    StepName.MAINTAIN_PREVIOUS,
    StepName.SAVE_CYCLE,
    StepName.UPDATE_REPORT,
    StepName.COMPLETE,
)

STEP_PLANS = {
    CycleKind.DAILY_FULL: DAILY_FULL_STEPS,
    CycleKind.INTRADAY_REBALANCE: (
        INTRADAY_REBALANCE_STEPS
    ),
    CycleKind.EXECUTION_REFRESH: (
        EXECUTION_REFRESH_STEPS
    ),
    CycleKind.MAINTENANCE_ONLY: (
        MAINTENANCE_ONLY_STEPS
    ),
}

STEP_TO_STAGE = {
    StepName.MAINTAIN_PREVIOUS: StageName.MAINTENANCE,
    StepName.REFRESH_BASE_DATA: StageName.BASE_DATA,
    StepName.DECIDE_CYCLE_KIND: StageName.BASE_DATA,
    StepName.RUN_COARSE: StageName.COARSE,
    StepName.RUN_PORTFOLIO: StageName.PORTFOLIO,
    StepName.COLLECT_REVIEW: StageName.REVIEW,
    StepName.REFRESH_EXECUTION_DATA: StageName.EXECUTION,
    StepName.RUN_EXECUTION: StageName.EXECUTION,
    StepName.BUILD_ORDERS: StageName.ORDERS,
    StepName.VALIDATE_ORDERS: StageName.ORDERS,
    StepName.SUBMIT_ORDERS: StageName.ORDERS,
    StepName.UPDATE_REPORT: StageName.REPORT,
}


@dataclass(frozen=True)
class CycleKindInputs:
    force_full: bool = False
    force_rebalance: bool = False
    execution_only: bool = False
    maintenance_only: bool = False


def decide_cycle_kind(
    daily_state: DailyState,
    inputs: CycleKindInputs,
) -> CycleKind:
    """Make the startup classification using only Phase A facts.

    After Phase B refreshes broker state, ``execution_refresh`` may be upgraded
    to ``intraday_rebalance``.  The startup decision therefore stays
    intentionally conservative.
    """

    requested_modes = sum(
        bool(value)
        for value in (
            inputs.force_full,
            inputs.force_rebalance,
            inputs.execution_only,
            inputs.maintenance_only,
        )
    )
    if requested_modes > 1:
        raise StateValidationError(
            "轮次模式参数互相冲突",
            code="CONFLICTING_CYCLE_MODES",
        )

    if inputs.maintenance_only:
        return CycleKind.MAINTENANCE_ONLY
    if inputs.execution_only:
        return CycleKind.EXECUTION_REFRESH
    if inputs.force_full:
        return CycleKind.DAILY_FULL
    if inputs.force_rebalance:
        return CycleKind.INTRADAY_REBALANCE

    first_daily_run = (
        daily_state.first_successful_cycle_id is None
        or not daily_state.detailed_report_created
        or daily_state.coarse_status != CoarseStatus.VALID
    )
    if first_daily_run:
        return CycleKind.DAILY_FULL

    return CycleKind.EXECUTION_REFRESH


def step_plan(cycle_kind: CycleKind) -> tuple[StepName, ...]:
    return STEP_PLANS[cycle_kind]


def _planned_stage_steps(
    state: CycleState,
    stage: StageName,
) -> tuple[StepName, ...]:
    return tuple(
        step
        for step in step_plan(state.cycle_kind)
        if STEP_TO_STAGE.get(step) == stage
    )


def prepare_state(state: CycleState) -> None:
    """Mark stages omitted by the selected cycle kind as skipped."""

    if state.status in TERMINAL_CYCLE_STATUSES:
        return

    now = utc_now_iso()
    planned_stages = {
        stage
        for step in step_plan(state.cycle_kind)
        if (stage := STEP_TO_STAGE.get(step)) is not None
    }

    for stage in StageName:
        if stage in planned_stages:
            continue

        record = state.stages[stage.value]
        if record.status == StageStatus.PENDING:
            record.status = StageStatus.SKIPPED
            record.started_at = now
            record.completed_at = now
            record.message = (
                f"{state.cycle_kind.value}不需要该阶段"
            )

    state.updated_at = now


def validate_resume_compatibility(
    state: CycleState,
    *,
    config_version: str,
    config_signature: str,
) -> None:
    """Reject a resume if risk/config identity changed mid-cycle."""

    if (
        state.config_version != config_version
        or state.config_signature != config_signature
    ):
        raise StateValidationError(
            "轮次配置与当前v2配置不一致，不能原地恢复",
            code="CYCLE_CONFIG_MISMATCH",
            details={
                "cycle_config_version": (
                    state.config_version
                ),
                "current_config_version": config_version,
            },
        )


def next_step(state: CycleState) -> StepName | None:
    """Return the first unfinished planned step."""

    state.validate()
    if state.status in TERMINAL_CYCLE_STATUSES:
        return None
    if not state.resume_allowed:
        raise StateValidationError(
            "轮次不允许恢复",
            code="RESUME_NOT_ALLOWED",
        )

    plan = step_plan(state.cycle_kind)
    unexpected = set(state.completed_steps) - set(plan)
    if unexpected:
        raise StateValidationError(
            "状态包含当前轮次类型之外的已完成步骤",
            code="STEP_PLAN_MISMATCH",
            details={
                "unexpected_steps": sorted(
                    step.value for step in unexpected
                )
            },
        )

    for step in plan:
        if step not in state.completed_steps:
            return step
    return None


def begin_next_step(state: CycleState) -> StepName:
    """Start or retry the next persisted step."""

    prepare_state(state)
    step = next_step(state)
    if step is None:
        raise StateValidationError(
            "轮次没有可开始的步骤",
            code="NO_PENDING_STEP",
        )

    now = utc_now_iso()
    state.current_step = step
    state.status = CycleStatus.RUNNING
    state.failed_step = None
    state.step_attempts[step.value] = (
        state.step_attempts.get(step.value, 0) + 1
    )

    stage = STEP_TO_STAGE.get(step)
    if stage is not None:
        record = state.stages[stage.value]
        if record.status in {
            StageStatus.PENDING,
            StageStatus.FAILED,
        }:
            record.status = StageStatus.RUNNING
            record.attempts += 1
            record.started_at = now
            record.completed_at = None
            record.message = ""

    state.updated_at = now
    state.validate()
    return step


def pause_for_review(state: CycleState) -> None:
    if (
        state.current_step != StepName.COLLECT_REVIEW
        or state.status != CycleStatus.RUNNING
        or state.review_mode != ReviewMode.PROMPT
    ):
        raise StateValidationError(
            "当前状态不能等待人工意见",
            code="INVALID_REVIEW_PAUSE",
        )

    state.status = CycleStatus.WAITING_FOR_REVIEW
    state.updated_at = utc_now_iso()
    state.validate()


def _finish_stage_if_ready(
    state: CycleState,
    stage: StageName,
    *,
    output_path: str | None,
    message: str,
) -> None:
    stage_steps = _planned_stage_steps(
        state,
        stage,
    )
    if not stage_steps or not all(
        step in state.completed_steps
        for step in stage_steps
    ):
        return

    record = state.stages[stage.value]
    all_skipped = all(
        step in state.skipped_steps
        for step in stage_steps
    )
    record.status = (
        StageStatus.SKIPPED
        if all_skipped
        else StageStatus.COMPLETED
    )
    if record.started_at is None:
        record.started_at = utc_now_iso()
    record.completed_at = utc_now_iso()
    if output_path is not None:
        record.output_path = output_path
    record.message = message


def complete_current_step(
    state: CycleState,
    *,
    skipped: bool = False,
    output_path: str | None = None,
    message: str = "",
    final_status: CycleStatus = CycleStatus.COMPLETED,
) -> None:
    """Persist success for the current step.

    ``COMPLETE`` is the only step that accepts a terminal ``final_status``.
    No-action and open-order completion are normal successful terminal states.
    """

    step = state.current_step
    allowed_statuses = {
        CycleStatus.RUNNING,
        CycleStatus.WAITING_FOR_REVIEW,
    }
    if state.status not in allowed_statuses:
        raise StateValidationError(
            "只有运行中步骤可以完成",
            code="STEP_NOT_RUNNING",
        )
    if step == StepName.START:
        raise StateValidationError(
            "START不是可完成的执行步骤",
            code="INVALID_STEP_COMPLETION",
        )
    if step in state.completed_steps:
        raise StateValidationError(
            f"步骤已完成：{step.value}",
            code="DUPLICATE_STEP_COMPLETION",
        )

    expected = next_step(state)
    if step != expected:
        raise StateValidationError(
            (
                f"步骤完成顺序错误：current={step.value}；"
                f"expected={expected.value if expected else 'none'}"
            ),
            code="STEP_ORDER_VIOLATION",
        )

    state.completed_steps.append(step)
    if skipped:
        state.skipped_steps.append(step)
    state.failed_step = None
    state.updated_at = utc_now_iso()

    stage = STEP_TO_STAGE.get(step)
    if stage is not None:
        _finish_stage_if_ready(
            state,
            stage,
            output_path=output_path,
            message=message,
        )

    if step == StepName.COMPLETE:
        if skipped:
            raise StateValidationError(
                "COMPLETE步骤不能标记为skipped",
                code="INVALID_COMPLETE_SKIP",
            )
        if final_status not in {
            CycleStatus.COMPLETED,
            CycleStatus.COMPLETED_NO_ACTION,
            CycleStatus.COMPLETED_DRY_RUN,
            CycleStatus.COMPLETED_NO_SUBMISSION,
            CycleStatus.COMPLETED_WITH_SUBMISSIONS,
            CycleStatus.COMPLETED_WITH_OPEN_ORDERS,
        }:
            raise StateValidationError(
                "COMPLETE步骤的final_status不是成功终态",
                code="INVALID_FINAL_STATUS",
            )
        complete_cycle(
            state,
            status=final_status,
            stop_reason=message or None,
        )

    state.validate()


def fail_current_step(
    state: CycleState,
    error: BaseException,
) -> ErrorCategory:
    """Apply the exception taxonomy to the current persisted step."""

    if (
        state.current_step == StepName.START
        or state.status
        not in {
            CycleStatus.RUNNING,
            CycleStatus.WAITING_FOR_REVIEW,
        }
    ):
        raise StateValidationError(
            "当前没有可失败的运行中步骤",
            code="NO_RUNNING_STEP",
        )

    disposition = classify_exception(error)
    message = StateMessage(
        code=disposition.code,
        message=disposition.message,
        details=disposition.details,
    )
    stage = STEP_TO_STAGE.get(
        state.current_step
    )
    if stage is not None:
        record = state.stages[stage.value]
        record.status = (
            StageStatus.BLOCKED
            if disposition.category
            == ErrorCategory.SAFETY_BLOCK
            else StageStatus.FAILED
        )
        if record.started_at is None:
            record.started_at = utc_now_iso()
        record.completed_at = utc_now_iso()
        record.message = disposition.message

    state.failed_step = state.current_step
    state.stop_reason = disposition.message

    if disposition.category == ErrorCategory.RETRYABLE:
        state.errors.append(message)
        state.status = CycleStatus.FAILED_RETRIABLE
        state.resume_allowed = True
        state.updated_at = utc_now_iso()
    elif disposition.category == ErrorCategory.SAFETY_BLOCK:
        state.warnings.append(message)
        complete_cycle(
            state,
            status=CycleStatus.BLOCKED,
            stop_reason=disposition.message,
        )
    else:
        state.errors.append(message)
        complete_cycle(
            state,
            status=CycleStatus.FAILED_TERMINAL,
            stop_reason=disposition.message,
        )

    state.validate()
    return disposition.category


def mark_daily_cycle_terminal(
    daily_state: DailyState,
    cycle_state: CycleState,
) -> None:
    """Reflect a terminal cycle in the date-level index."""

    if cycle_state.status not in TERMINAL_CYCLE_STATUSES:
        raise StateValidationError(
            "只能登记终止轮次",
            code="CYCLE_NOT_TERMINAL",
        )
    if cycle_state.cycle_id not in daily_state.cycle_ids:
        raise StateValidationError(
            "轮次尚未登记到daily_state",
            code="CYCLE_NOT_REGISTERED",
        )

    if (
        daily_state.active_cycle_id
        == cycle_state.cycle_id
    ):
        daily_state.active_cycle_id = None

    successful = {
        CycleStatus.COMPLETED,
        CycleStatus.COMPLETED_NO_ACTION,
        CycleStatus.COMPLETED_DRY_RUN,
        CycleStatus.COMPLETED_NO_SUBMISSION,
        CycleStatus.COMPLETED_WITH_SUBMISSIONS,
        CycleStatus.COMPLETED_WITH_OPEN_ORDERS,
    }
    if (
        cycle_state.status in successful
        and daily_state.first_successful_cycle_id is None
    ):
        daily_state.first_successful_cycle_id = (
            cycle_state.cycle_id
        )

    daily_state.latest_cycle_id = cycle_state.cycle_id
    daily_state.updated_at = utc_now_iso()
    daily_state.validate()


def planned_steps_without(
    cycle_kind: CycleKind,
    omitted: Iterable[StepName],
) -> tuple[StepName, ...]:
    """Small test/diagnostic helper for displaying reuse decisions."""

    omitted_set = set(omitted)
    return tuple(
        step
        for step in step_plan(cycle_kind)
        if step not in omitted_set
    )
