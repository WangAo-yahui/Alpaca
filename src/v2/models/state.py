"""定义并持久化 WA Trader v2 的日期级与轮次级状态模型。

作用：记录步骤、失败策略、profile、release、guidance、恢复点和交易许可。
重要性：状态是断点恢复与失败关闭的事实来源，任何不一致都必须在继续决策前被拒绝。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


# 允许直接运行：
# python3 -u src/v2/models/state.py
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[3]
    src_root = project_root / "src"

    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


from v2.runtime import (  # noqa: E402
    CyclePaths,
    DailyPaths,
    atomic_write_json,
    build_cycle_paths,
    build_daily_paths,
    cycle_id_to_run_date,
    load_json_object,
    new_york_now_iso,
    normalize_cycle_id,
    normalize_run_date,
    utc_now_iso,
)


SCRIPT_VERSION = "2026-07-25-v2-state-models-stage-g-v1"
SCHEMA_VERSION = "1.0"


class CycleKind(StrEnum):
    DAILY_FULL = "daily_full"
    INTRADAY_REBALANCE = "intraday_rebalance"
    EXECUTION_REFRESH = "execution_refresh"
    MAINTENANCE_ONLY = "maintenance_only"


class ReviewMode(StrEnum):
    PROMPT = "prompt"
    SKIPPED_BY_FLAG = "skipped_by_flag"


class CycleStatus(StrEnum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"

    BLOCKED = "blocked"
    FAILED_RETRIABLE = "failed_retriable"
    FAILED_TERMINAL = "failed_terminal"

    COMPLETED = "completed"
    COMPLETED_NO_ACTION = "completed_no_action"
    COMPLETED_DRY_RUN = "completed_dry_run"
    COMPLETED_NO_SUBMISSION = (
        "completed_no_submission"
    )
    COMPLETED_WITH_SUBMISSIONS = (
        "completed_with_submissions"
    )
    COMPLETED_WITH_OPEN_ORDERS = (
        "completed_with_open_orders"
    )
    COMPLETED_WITH_PARTIAL_FILLS = (
        "completed_with_partial_fills"
    )
    COMPLETED_WITH_REJECTIONS = (
        "completed_with_rejections"
    )
    BLOCKED_SUBMISSION_UNCERTAIN = (
        "blocked_submission_uncertain"
    )


class StepName(StrEnum):
    START = "START"
    MAINTAIN_PREVIOUS = "MAINTAIN_PREVIOUS"
    REFRESH_BASE_DATA = "REFRESH_BASE_DATA"
    DECIDE_CYCLE_KIND = "DECIDE_CYCLE_KIND"
    RUN_COARSE = "RUN_COARSE"
    RUN_PORTFOLIO = "RUN_PORTFOLIO"
    COLLECT_REVIEW = "COLLECT_REVIEW"
    REFRESH_EXECUTION_DATA = "REFRESH_EXECUTION_DATA"
    RUN_EXECUTION = "RUN_EXECUTION"
    BUILD_ORDERS = "BUILD_ORDERS"
    VALIDATE_ORDERS = "VALIDATE_ORDERS"
    SUBMIT_ORDERS = "SUBMIT_ORDERS"
    SAVE_CYCLE = "SAVE_CYCLE"
    UPDATE_REPORT = "UPDATE_REPORT"
    COMPLETE = "COMPLETE"


class StageName(StrEnum):
    MAINTENANCE = "maintenance"
    BASE_DATA = "base_data"
    COARSE = "coarse"
    PORTFOLIO = "portfolio"
    REVIEW = "review"
    EXECUTION = "execution"
    ORDERS = "orders"
    REPORT = "report"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class CoarseStatus(StrEnum):
    MISSING = "missing"
    RUNNING = "running"
    VALID = "valid"
    INVALID = "invalid"
    FAILED = "failed"


class SessionPolicy(StrEnum):
    ANALYSIS_ONLY = "analysis_only"
    BROKER_CAPABILITY = "broker_capability"


@dataclass(frozen=True)
class InvocationState:
    no_review: bool
    allow_trade: bool
    paper: bool
    live: bool

    def validate(self) -> None:
        if self.live == self.paper:
            raise ValueError(
                "v2状态必须且只能选择paper或live之一"
            )

    def to_dict(self) -> dict[str, bool]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "InvocationState":
        state = cls(
            no_review=bool(
                payload.get("no_review", False)
            ),
            allow_trade=bool(
                payload.get("allow_trade", False)
            ),
            paper=bool(
                payload.get("paper", True)
            ),
            live=bool(
                payload.get("live", False)
            ),
        )
        state.validate()
        return state


@dataclass(frozen=True)
class TradePermission:
    submission_enabled: bool
    extended_hours_requested: bool
    session_policy: SessionPolicy
    dry_run: bool

    def validate(self) -> None:
        enabled = self.submission_enabled
        if self.dry_run == enabled:
            raise ValueError(
                "dry_run必须与submission_enabled相反"
            )
        expected_policy = (
            SessionPolicy.BROKER_CAPABILITY
            if enabled
            else SessionPolicy.ANALYSIS_ONLY
        )
        if self.session_policy != expected_policy:
            raise ValueError(
                "trade_permission.session_policy不一致"
            )
        if self.extended_hours_requested != enabled:
            raise ValueError(
                "extended_hours_requested必须与提交权限一致"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "submission_enabled": (
                self.submission_enabled
            ),
            "extended_hours_requested": (
                self.extended_hours_requested
            ),
            "session_policy": (
                self.session_policy.value
            ),
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "TradePermission":
        state = cls(
            submission_enabled=bool(
                payload.get(
                    "submission_enabled",
                    False,
                )
            ),
            extended_hours_requested=bool(
                payload.get(
                    "extended_hours_requested",
                    False,
                )
            ),
            session_policy=SessionPolicy(
                payload.get(
                    "session_policy",
                    SessionPolicy.ANALYSIS_ONLY.value,
                )
            ),
            dry_run=bool(
                payload.get("dry_run", True)
            ),
        )
        state.validate()
        return state


def invocation_state(
    *,
    no_review: bool,
    allow_trade: bool,
    paper: bool = True,
    live: bool = False,
) -> InvocationState:
    state = InvocationState(
        no_review=no_review,
        allow_trade=allow_trade,
        paper=paper,
        live=live,
    )
    state.validate()
    return state


def trade_permission(
    allow_trade: bool,
) -> TradePermission:
    state = TradePermission(
        submission_enabled=allow_trade,
        extended_hours_requested=allow_trade,
        session_policy=(
            SessionPolicy.BROKER_CAPABILITY
            if allow_trade
            else SessionPolicy.ANALYSIS_ONLY
        ),
        dry_run=not allow_trade,
    )
    state.validate()
    return state


TERMINAL_CYCLE_STATUSES = {
    CycleStatus.BLOCKED,
    CycleStatus.FAILED_TERMINAL,
    CycleStatus.COMPLETED,
    CycleStatus.COMPLETED_NO_ACTION,
    CycleStatus.COMPLETED_DRY_RUN,
    CycleStatus.COMPLETED_NO_SUBMISSION,
    CycleStatus.COMPLETED_WITH_SUBMISSIONS,
    CycleStatus.COMPLETED_WITH_OPEN_ORDERS,
    CycleStatus.COMPLETED_WITH_PARTIAL_FILLS,
    CycleStatus.COMPLETED_WITH_REJECTIONS,
    CycleStatus.BLOCKED_SUBMISSION_UNCERTAIN,
}


RESUMABLE_CYCLE_STATUSES = {
    CycleStatus.INITIALIZED,
    CycleStatus.RUNNING,
    CycleStatus.WAITING_FOR_REVIEW,
    CycleStatus.FAILED_RETRIABLE,
}


@dataclass
class StateMessage:
    code: str
    message: str
    created_at: str = field(
        default_factory=utc_now_iso
    )
    details: dict[str, Any] = field(
        default_factory=dict
    )

    def validate(self) -> None:
        if not self.code.strip():
            raise ValueError(
                "StateMessage.code不能为空"
            )

        if not self.message.strip():
            raise ValueError(
                "StateMessage.message不能为空"
            )

        if not isinstance(self.details, dict):
            raise TypeError(
                "StateMessage.details必须是对象"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "StateMessage":
        message = cls(
            code=str(payload.get("code", "")),
            message=str(
                payload.get("message", "")
            ),
            created_at=str(
                payload.get(
                    "created_at",
                    utc_now_iso(),
                )
            ),
            details=(
                payload.get("details", {})
                if isinstance(
                    payload.get("details", {}),
                    dict,
                )
                else {}
            ),
        )
        message.validate()
        return message


@dataclass
class StageRecord:
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    output_path: str | None = None
    message: str = ""

    def validate(self) -> None:
        if self.attempts < 0:
            raise ValueError(
                "StageRecord.attempts不能小于0"
            )

        if (
            self.status == StageStatus.RUNNING
            and self.started_at is None
        ):
            raise ValueError(
                "运行中阶段必须有started_at"
            )

        if (
            self.status
            in {
                StageStatus.COMPLETED,
                StageStatus.SKIPPED,
                StageStatus.BLOCKED,
                StageStatus.FAILED,
            }
            and self.completed_at is None
        ):
            raise ValueError(
                "已结束阶段必须有completed_at"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()

        payload = asdict(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "StageRecord":
        record = cls(
            status=StageStatus(
                payload.get(
                    "status",
                    StageStatus.PENDING.value,
                )
            ),
            attempts=int(
                payload.get("attempts", 0)
            ),
            started_at=payload.get(
                "started_at"
            ),
            completed_at=payload.get(
                "completed_at"
            ),
            output_path=payload.get(
                "output_path"
            ),
            message=str(
                payload.get("message", "")
            ),
        )
        record.validate()
        return record


def default_stage_records() -> dict[str, StageRecord]:
    return {
        stage.value: StageRecord()
        for stage in StageName
    }


@dataclass
class DailyState:
    schema_version: str
    run_date: str
    profile_id: str
    strategy_id: str
    strategy_version: str
    config_version: str
    config_signature: str
    first_successful_cycle_id: str | None
    latest_cycle_id: str | None
    active_cycle_id: str | None

    coarse_status: CoarseStatus
    coarse_output_path: str | None
    coarse_input_signature: str | None
    latest_valid_portfolio_cycle_id: str | None
    latest_valid_portfolio_output_path: str | None
    latest_portfolio_input_signature: str | None
    latest_portfolio_valid_until: str | None

    detailed_report_created: bool
    cycle_ids: list[str]

    created_at: str
    updated_at: str

    warnings: list[StateMessage] = field(
        default_factory=list
    )
    errors: list[StateMessage] = field(
        default_factory=list
    )

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "DailyState schema_version不支持："
                f"{self.schema_version}"
            )

        normalized_date = normalize_run_date(
            self.run_date
        )

        if normalized_date != self.run_date:
            raise ValueError(
                "DailyState.run_date必须规范化"
            )

        if not self.config_version.strip():
            raise ValueError(
                "DailyState.config_version不能为空"
            )

        if not self.config_signature.strip():
            raise ValueError(
                "DailyState.config_signature不能为空"
            )

        for label, value in (
            ("profile_id", self.profile_id),
            ("strategy_id", self.strategy_id),
            (
                "strategy_version",
                self.strategy_version,
            ),
        ):
            if not value.strip():
                raise ValueError(
                    f"DailyState.{label}不能为空"
                )

        if len(set(self.cycle_ids)) != len(
            self.cycle_ids
        ):
            raise ValueError(
                "DailyState.cycle_ids不能重复"
            )

        for cycle_id in self.cycle_ids:
            normalize_cycle_id(cycle_id)
            if (
                cycle_id_to_run_date(cycle_id)
                != self.run_date
            ):
                raise ValueError(
                    "daily_state包含其他日期的cycle_id"
                )

        if (
            self.active_cycle_id is not None
            and self.active_cycle_id
            not in self.cycle_ids
        ):
            raise ValueError(
                "active_cycle_id必须存在于cycle_ids"
            )

        if (
            self.latest_cycle_id is not None
            and self.latest_cycle_id
            not in self.cycle_ids
        ):
            raise ValueError(
                "latest_cycle_id必须存在于cycle_ids"
            )

        if (
            self.first_successful_cycle_id
            is not None
            and self.first_successful_cycle_id
            not in self.cycle_ids
        ):
            raise ValueError(
                "first_successful_cycle_id"
                "必须存在于cycle_ids"
            )

        if (
            self.latest_valid_portfolio_cycle_id
            is not None
            and self.latest_valid_portfolio_cycle_id
            not in self.cycle_ids
        ):
            raise ValueError(
                "latest_valid_portfolio_cycle_id"
                "必须存在于cycle_ids"
            )

        for item in self.warnings:
            item.validate()

        for item in self.errors:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()

        return {
            "schema_version": self.schema_version,
            "run_date": self.run_date,
            "profile_id": self.profile_id,
            "strategy_id": self.strategy_id,
            "strategy_version": (
                self.strategy_version
            ),
            "config_version": self.config_version,
            "config_signature": self.config_signature,
            "first_successful_cycle_id": (
                self.first_successful_cycle_id
            ),
            "latest_cycle_id": (
                self.latest_cycle_id
            ),
            "active_cycle_id": (
                self.active_cycle_id
            ),
            "coarse_status": (
                self.coarse_status.value
            ),
            "coarse_output_path": (
                self.coarse_output_path
            ),
            "coarse_input_signature": (
                self.coarse_input_signature
            ),
            "latest_valid_portfolio_cycle_id": (
                self.latest_valid_portfolio_cycle_id
            ),
            "latest_valid_portfolio_output_path": (
                self.latest_valid_portfolio_output_path
            ),
            "latest_portfolio_input_signature": (
                self.latest_portfolio_input_signature
            ),
            "latest_portfolio_valid_until": (
                self.latest_portfolio_valid_until
            ),
            "detailed_report_created": (
                self.detailed_report_created
            ),
            "cycle_ids": list(self.cycle_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "warnings": [
                item.to_dict()
                for item in self.warnings
            ],
            "errors": [
                item.to_dict()
                for item in self.errors
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DailyState":
        state = cls(
            schema_version=str(
                payload.get(
                    "schema_version",
                    "",
                )
            ),
            run_date=str(
                payload.get("run_date", "")
            ),
            profile_id=str(
                payload.get(
                    "profile_id",
                    "default",
                )
            ),
            strategy_id=str(
                payload.get(
                    "strategy_id",
                    "core_long",
                )
            ),
            strategy_version=str(
                payload.get(
                    "strategy_version",
                    "1.0.0",
                )
            ),
            config_version=str(
                payload.get(
                    "config_version",
                    "legacy-unversioned",
                )
            ),
            config_signature=str(
                payload.get(
                    "config_signature",
                    "legacy-unversioned",
                )
            ),
            first_successful_cycle_id=(
                payload.get(
                    "first_successful_cycle_id"
                )
            ),
            latest_cycle_id=payload.get(
                "latest_cycle_id"
            ),
            active_cycle_id=payload.get(
                "active_cycle_id"
            ),
            coarse_status=CoarseStatus(
                payload.get(
                    "coarse_status",
                    CoarseStatus.MISSING.value,
                )
            ),
            coarse_output_path=payload.get(
                "coarse_output_path"
            ),
            coarse_input_signature=payload.get(
                "coarse_input_signature"
            ),
            latest_valid_portfolio_cycle_id=(
                payload.get(
                    "latest_valid_portfolio_cycle_id"
                )
            ),
            latest_valid_portfolio_output_path=(
                payload.get(
                    "latest_valid_portfolio_output_path"
                )
            ),
            latest_portfolio_input_signature=(
                payload.get(
                    "latest_portfolio_input_signature"
                )
            ),
            latest_portfolio_valid_until=(
                payload.get(
                    "latest_portfolio_valid_until"
                )
            ),
            detailed_report_created=bool(
                payload.get(
                    "detailed_report_created",
                    False,
                )
            ),
            cycle_ids=[
                str(value)
                for value in payload.get(
                    "cycle_ids",
                    [],
                )
            ],
            created_at=str(
                payload.get(
                    "created_at",
                    utc_now_iso(),
                )
            ),
            updated_at=str(
                payload.get(
                    "updated_at",
                    utc_now_iso(),
                )
            ),
            warnings=[
                StateMessage.from_dict(item)
                for item in payload.get(
                    "warnings",
                    [],
                )
                if isinstance(item, dict)
            ],
            errors=[
                StateMessage.from_dict(item)
                for item in payload.get(
                    "errors",
                    [],
                )
                if isinstance(item, dict)
            ],
        )
        state.validate()
        return state


@dataclass
class CycleState:
    schema_version: str
    run_date: str
    cycle_id: str
    cycle_kind: CycleKind
    status: CycleStatus
    current_step: StepName
    review_mode: ReviewMode
    config_version: str
    config_signature: str
    invocation: InvocationState
    trade_permission: TradePermission
    profile_id: str
    release: dict[str, Any]
    guidance: dict[str, Any]

    created_at: str
    updated_at: str
    started_at_new_york: str
    completed_at: str | None

    stages: dict[str, StageRecord]
    completed_steps: list[StepName]
    skipped_steps: list[StepName]
    step_attempts: dict[str, int]

    resume_allowed: bool
    resume_count: int

    previous_cycle_id: str | None
    reused_coarse_cycle_id: str | None
    reused_portfolio_cycle_id: str | None

    stop_reason: str | None
    failed_step: StepName | None
    warnings: list[StateMessage] = field(
        default_factory=list
    )
    errors: list[StateMessage] = field(
        default_factory=list
    )

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "CycleState schema_version不支持："
                f"{self.schema_version}"
            )

        if normalize_run_date(
            self.run_date
        ) != self.run_date:
            raise ValueError(
                "CycleState.run_date必须规范化"
            )

        normalize_cycle_id(self.cycle_id)
        if (
            cycle_id_to_run_date(self.cycle_id)
            != self.run_date
        ):
            raise ValueError(
                "cycle_id日期与run_date不一致"
            )

        if self.resume_count < 0:
            raise ValueError(
                "resume_count不能小于0"
            )

        if not self.config_version.strip():
            raise ValueError(
                "CycleState.config_version不能为空"
            )

        if not self.config_signature.strip():
            raise ValueError(
                "CycleState.config_signature不能为空"
            )

        if not self.profile_id.strip():
            raise ValueError(
                "CycleState.profile_id不能为空"
            )
        required_release = {
            "app_version",
            "git_commit",
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
        }
        if (
            not isinstance(self.release, dict)
            or not required_release.issubset(
                self.release
            )
        ):
            raise ValueError(
                "CycleState.release不完整"
            )
        if not isinstance(self.guidance, dict):
            raise ValueError(
                "CycleState.guidance必须是对象"
            )
        if not {
            "path",
            "guidance_hash",
        }.issubset(self.guidance):
            raise ValueError(
                "CycleState.guidance不完整"
            )

        self.invocation.validate()
        self.trade_permission.validate()
        if (
            self.invocation.allow_trade
            != self.trade_permission.submission_enabled
        ):
            raise ValueError(
                "invocation与trade_permission不一致"
            )

        if len(set(self.completed_steps)) != len(
            self.completed_steps
        ):
            raise ValueError(
                "CycleState.completed_steps不能重复"
            )

        if StepName.START in self.completed_steps:
            raise ValueError(
                "START不能写入completed_steps"
            )

        if len(set(self.skipped_steps)) != len(
            self.skipped_steps
        ):
            raise ValueError(
                "CycleState.skipped_steps不能重复"
            )

        if not set(self.skipped_steps).issubset(
            self.completed_steps
        ):
            raise ValueError(
                "skipped_steps必须是completed_steps子集"
            )

        for name, attempts in self.step_attempts.items():
            try:
                StepName(name)
            except ValueError as error:
                raise ValueError(
                    f"未知step_attempts键：{name}"
                ) from error

            if attempts < 0:
                raise ValueError(
                    "step_attempts次数不能小于0"
                )

        expected_stages = {
            stage.value
            for stage in StageName
        }

        actual_stages = set(
            self.stages.keys()
        )

        if expected_stages != actual_stages:
            raise ValueError(
                "CycleState.stages键不完整："
                f"缺少={sorted(expected_stages - actual_stages)}；"
                f"多余={sorted(actual_stages - expected_stages)}"
            )

        for record in self.stages.values():
            record.validate()

        for item in self.warnings:
            item.validate()

        for item in self.errors:
            item.validate()

        if (
            self.status in TERMINAL_CYCLE_STATUSES
            and self.current_step
            != StepName.COMPLETE
        ):
            raise ValueError(
                "终止状态的current_step必须为COMPLETE"
            )

        if (
            self.status in TERMINAL_CYCLE_STATUSES
            and self.completed_at is None
        ):
            raise ValueError(
                "终止状态必须有completed_at"
            )

        if (
            self.status not in TERMINAL_CYCLE_STATUSES
            and self.completed_at is not None
        ):
            raise ValueError(
                "非终止状态不能有completed_at"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()

        return {
            "schema_version": self.schema_version,
            "run_date": self.run_date,
            "cycle_id": self.cycle_id,
            "cycle_kind": self.cycle_kind.value,
            "status": self.status.value,
            "current_step": self.current_step.value,
            "review_mode": self.review_mode.value,
            "config_version": self.config_version,
            "config_signature": self.config_signature,
            "invocation": self.invocation.to_dict(),
            "trade_permission": (
                self.trade_permission.to_dict()
            ),
            "profile_id": self.profile_id,
            "release": dict(self.release),
            "guidance": dict(self.guidance),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at_new_york": (
                self.started_at_new_york
            ),
            "completed_at": self.completed_at,
            "stages": {
                name: record.to_dict()
                for name, record
                in self.stages.items()
            },
            "completed_steps": [
                step.value
                for step in self.completed_steps
            ],
            "skipped_steps": [
                step.value
                for step in self.skipped_steps
            ],
            "step_attempts": dict(
                self.step_attempts
            ),
            "resume": {
                "allowed": self.resume_allowed,
                "resume_count": self.resume_count,
            },
            "previous_cycle_id": (
                self.previous_cycle_id
            ),
            "reused_coarse_cycle_id": (
                self.reused_coarse_cycle_id
            ),
            "reused_portfolio_cycle_id": (
                self.reused_portfolio_cycle_id
            ),
            "stop_reason": self.stop_reason,
            "failed_step": (
                self.failed_step.value
                if self.failed_step is not None
                else None
            ),
            "warnings": [
                item.to_dict()
                for item in self.warnings
            ],
            "errors": [
                item.to_dict()
                for item in self.errors
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "CycleState":
        resume = payload.get(
            "resume",
            {},
        )

        if not isinstance(resume, dict):
            resume = {}

        raw_stages = payload.get(
            "stages",
            {},
        )

        if not isinstance(raw_stages, dict):
            raise ValueError(
                "CycleState.stages必须是对象"
            )

        stages = {
            name: StageRecord.from_dict(
                record
            )
            for name, record
            in raw_stages.items()
            if isinstance(record, dict)
        }

        raw_invocation = payload.get(
            "invocation",
            {},
        )
        if not isinstance(raw_invocation, dict):
            raw_invocation = {}

        raw_permission = payload.get(
            "trade_permission",
            {},
        )
        if not isinstance(raw_permission, dict):
            raw_permission = {}

        state = cls(
            schema_version=str(
                payload.get(
                    "schema_version",
                    "",
                )
            ),
            run_date=str(
                payload.get("run_date", "")
            ),
            cycle_id=str(
                payload.get("cycle_id", "")
            ),
            cycle_kind=CycleKind(
                payload.get(
                    "cycle_kind",
                    CycleKind.DAILY_FULL.value,
                )
            ),
            status=CycleStatus(
                payload.get(
                    "status",
                    CycleStatus.INITIALIZED.value,
                )
            ),
            current_step=StepName(
                payload.get(
                    "current_step",
                    StepName.START.value,
                )
            ),
            review_mode=ReviewMode(
                payload.get(
                    "review_mode",
                    ReviewMode.PROMPT.value,
                )
            ),
            config_version=str(
                payload.get(
                    "config_version",
                    "legacy-unversioned",
                )
            ),
            config_signature=str(
                payload.get(
                    "config_signature",
                    "legacy-unversioned",
                )
            ),
            invocation=InvocationState.from_dict(
                raw_invocation
            ),
            trade_permission=(
                TradePermission.from_dict(
                    raw_permission
                )
            ),
            profile_id=str(
                payload.get(
                    "profile_id",
                    "default",
                )
            ),
            release=(
                dict(payload["release"])
                if isinstance(
                    payload.get("release"),
                    dict,
                )
                else {
                    "app_version": "2.0.0",
                    "git_commit": "unknown",
                    "strategy_id": "core_long",
                    "strategy_version": "1.0.0",
                    "risk_profile": (
                        "paper_standard@1.0.0"
                    ),
                    "release_hash": "unknown",
                    "prompt_hashes": {},
                    "schema_hashes": {},
                    "config_hashes": {},
                }
            ),
            guidance=(
                dict(payload["guidance"])
                if isinstance(
                    payload.get("guidance"),
                    dict,
                )
                else {
                    "path": "",
                    "guidance_hash": (
                        "e3b0c44298fc1c149afbf4c8996fb924"
                        "27ae41e4649b934ca495991b7852b855"
                    ),
                }
            ),
            created_at=str(
                payload.get(
                    "created_at",
                    utc_now_iso(),
                )
            ),
            updated_at=str(
                payload.get(
                    "updated_at",
                    utc_now_iso(),
                )
            ),
            started_at_new_york=str(
                payload.get(
                    "started_at_new_york",
                    new_york_now_iso(),
                )
            ),
            completed_at=payload.get(
                "completed_at"
            ),
            stages=stages,
            completed_steps=[
                StepName(value)
                for value in payload.get(
                    "completed_steps",
                    [],
                )
            ],
            skipped_steps=[
                StepName(value)
                for value in payload.get(
                    "skipped_steps",
                    [],
                )
            ],
            step_attempts={
                str(name): int(attempts)
                for name, attempts in (
                    payload.get(
                        "step_attempts",
                        {},
                    ).items()
                    if isinstance(
                        payload.get(
                            "step_attempts",
                            {},
                        ),
                        dict,
                    )
                    else []
                )
            },
            resume_allowed=bool(
                resume.get("allowed", True)
            ),
            resume_count=int(
                resume.get(
                    "resume_count",
                    0,
                )
            ),
            previous_cycle_id=payload.get(
                "previous_cycle_id"
            ),
            reused_coarse_cycle_id=(
                payload.get(
                    "reused_coarse_cycle_id"
                )
            ),
            reused_portfolio_cycle_id=(
                payload.get(
                    "reused_portfolio_cycle_id"
                )
            ),
            stop_reason=payload.get(
                "stop_reason"
            ),
            failed_step=(
                StepName(payload["failed_step"])
                if payload.get("failed_step")
                else None
            ),
            warnings=[
                StateMessage.from_dict(item)
                for item in payload.get(
                    "warnings",
                    [],
                )
                if isinstance(item, dict)
            ],
            errors=[
                StateMessage.from_dict(item)
                for item in payload.get(
                    "errors",
                    [],
                )
                if isinstance(item, dict)
            ],
        )
        state.release.setdefault(
            "risk_profile_hash",
            "unknown",
        )
        state.release.setdefault(
            "order_policy",
            "legacy-unversioned",
        )
        state.release.setdefault(
            "order_policy_hash",
            "unknown",
        )
        state.release.setdefault(
            "submission_policy",
            "legacy-unversioned",
        )
        state.release.setdefault(
            "submission_policy_hash",
            "unknown",
        )
        state.validate()
        return state


def new_daily_state(
    paths: DailyPaths,
    *,
    config_version: str = "phase-a-unconfigured",
    config_signature: str = "phase-a-unconfigured",
) -> DailyState:
    """创建新的日期级状态对象。"""
    now = utc_now_iso()

    state = DailyState(
        schema_version=SCHEMA_VERSION,
        run_date=paths.run_date,
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
        config_version=config_version,
        config_signature=config_signature,
        first_successful_cycle_id=None,
        latest_cycle_id=None,
        active_cycle_id=None,
        coarse_status=CoarseStatus.MISSING,
        coarse_output_path=None,
        coarse_input_signature=None,
        latest_valid_portfolio_cycle_id=None,
        latest_valid_portfolio_output_path=None,
        latest_portfolio_input_signature=None,
        latest_portfolio_valid_until=None,
        detailed_report_created=False,
        cycle_ids=[],
        created_at=now,
        updated_at=now,
    )
    state.validate()
    return state


def new_cycle_state(
    paths: CyclePaths,
    *,
    cycle_kind: CycleKind,
    review_mode: ReviewMode,
    previous_cycle_id: str | None = None,
    config_version: str = "phase-a-unconfigured",
    config_signature: str = "phase-a-unconfigured",
    no_review: bool = False,
    allow_trade: bool = False,
    paper: bool = True,
    live: bool = False,
    release: dict[str, Any] | None = None,
    guidance: dict[str, Any] | None = None,
) -> CycleState:
    """创建新的轮次级状态对象。"""
    now = utc_now_iso()
    normalized_release = (
        dict(release)
        if release is not None
        else None
    )
    if normalized_release is not None:
        normalized_release.setdefault(
            "submission_policy",
            "legacy-unversioned",
        )
        normalized_release.setdefault(
            "submission_policy_hash",
            "unknown",
        )

    state = CycleState(
        schema_version=SCHEMA_VERSION,
        run_date=paths.run_date,
        cycle_id=paths.cycle_id,
        cycle_kind=cycle_kind,
        status=CycleStatus.INITIALIZED,
        current_step=StepName.START,
        review_mode=review_mode,
        config_version=config_version,
        config_signature=config_signature,
        invocation=invocation_state(
            no_review=no_review,
            allow_trade=allow_trade,
            paper=paper,
            live=live,
        ),
        trade_permission=trade_permission(
            allow_trade
        ),
        profile_id=paths.profile_id,
        release=(
            normalized_release
            if normalized_release is not None
            else {
                "app_version": "2.0.0",
                "git_commit": "unknown",
                "strategy_id": paths.strategy_id,
                "strategy_version": (
                    paths.strategy_version
                ),
                "risk_profile": (
                    "paper_standard@1.1.0"
                ),
                "risk_profile_hash": "unknown",
                "order_policy": (
                    "paper_equity@1.0.0"
                ),
                "order_policy_hash": "unknown",
                "submission_policy": (
                    "alpaca_paper@1.0.0"
                ),
                "submission_policy_hash": "unknown",
                "release_hash": "unknown",
                "prompt_hashes": {},
                "schema_hashes": {},
                "config_hashes": {},
            }
        ),
        guidance=(
            dict(guidance)
            if guidance is not None
            else {
                "path": str(
                    paths.initial_guidance
                ),
                "guidance_hash": (
                    "e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855"
                ),
            }
        ),
        created_at=now,
        updated_at=now,
        started_at_new_york=(
            new_york_now_iso()
        ),
        completed_at=None,
        stages=default_stage_records(),
        completed_steps=[],
        skipped_steps=[],
        step_attempts={},
        resume_allowed=True,
        resume_count=0,
        previous_cycle_id=previous_cycle_id,
        reused_coarse_cycle_id=None,
        reused_portfolio_cycle_id=None,
        stop_reason=None,
        failed_step=None,
    )
    state.validate()
    return state


def load_daily_state(
    path: Path,
) -> DailyState:
    return DailyState.from_dict(
        load_json_object(path)
    )


def save_daily_state(
    path: Path,
    state: DailyState,
) -> None:
    state.updated_at = utc_now_iso()
    atomic_write_json(
        path,
        state.to_dict(),
    )


def load_cycle_state(
    path: Path,
) -> CycleState:
    return CycleState.from_dict(
        load_json_object(path)
    )


def save_cycle_state(
    path: Path,
    state: CycleState,
) -> None:
    state.updated_at = utc_now_iso()
    atomic_write_json(
        path,
        state.to_dict(),
    )


def initialize_daily_state(
    paths: DailyPaths,
    *,
    overwrite: bool = False,
    config_version: str = "phase-a-unconfigured",
    config_signature: str = "phase-a-unconfigured",
) -> DailyState:
    if paths.daily_state.exists() and not overwrite:
        return load_daily_state(
            paths.daily_state
        )

    state = new_daily_state(
        paths,
        config_version=config_version,
        config_signature=config_signature,
    )
    save_daily_state(
        paths.daily_state,
        state,
    )
    return state


def initialize_cycle_state(
    paths: CyclePaths,
    *,
    cycle_kind: CycleKind,
    review_mode: ReviewMode,
    previous_cycle_id: str | None = None,
    overwrite: bool = False,
    config_version: str = "phase-a-unconfigured",
    config_signature: str = "phase-a-unconfigured",
    no_review: bool = False,
    allow_trade: bool = False,
    paper: bool = True,
    live: bool = False,
    release: dict[str, Any] | None = None,
    guidance: dict[str, Any] | None = None,
) -> CycleState:
    if paths.cycle_state.exists() and not overwrite:
        return load_cycle_state(
            paths.cycle_state
        )

    state = new_cycle_state(
        paths,
        cycle_kind=cycle_kind,
        review_mode=review_mode,
        previous_cycle_id=previous_cycle_id,
        config_version=config_version,
        config_signature=config_signature,
        no_review=no_review,
        allow_trade=allow_trade,
        paper=paper,
        live=live,
        release=release,
        guidance=guidance,
    )
    save_cycle_state(
        paths.cycle_state,
        state,
    )
    return state


def register_cycle(
    daily_state: DailyState,
    cycle_state: CycleState,
) -> None:
    """
    将轮次登记到日期状态。
    """
    if (
        cycle_state.cycle_id
        not in daily_state.cycle_ids
    ):
        daily_state.cycle_ids.append(
            cycle_state.cycle_id
        )

    daily_state.latest_cycle_id = (
        cycle_state.cycle_id
    )
    daily_state.active_cycle_id = (
        cycle_state.cycle_id
    )


def update_invocation(
    state: CycleState,
    *,
    no_review: bool,
    allow_trade: bool,
    paper: bool = True,
    live: bool = False,
) -> None:
    """Record the permissions supplied by the latest invocation."""

    state.invocation = invocation_state(
        no_review=no_review,
        allow_trade=allow_trade,
        paper=paper,
        live=live,
    )
    state.trade_permission = trade_permission(
        allow_trade
    )
    state.review_mode = (
        ReviewMode.SKIPPED_BY_FLAG
        if no_review
        else ReviewMode.PROMPT
    )
    state.updated_at = utc_now_iso()
    state.validate()


def start_stage(
    state: CycleState,
    stage_name: StageName,
    step_name: StepName,
) -> None:
    record = state.stages[
        stage_name.value
    ]

    record.status = StageStatus.RUNNING
    record.attempts += 1
    record.started_at = utc_now_iso()
    record.completed_at = None
    record.message = ""

    state.status = CycleStatus.RUNNING
    state.current_step = step_name
    state.failed_step = None
    state.updated_at = utc_now_iso()


def finish_stage(
    state: CycleState,
    stage_name: StageName,
    *,
    status: StageStatus = StageStatus.COMPLETED,
    output_path: str | None = None,
    message: str = "",
) -> None:
    if status not in {
        StageStatus.COMPLETED,
        StageStatus.SKIPPED,
        StageStatus.BLOCKED,
        StageStatus.FAILED,
    }:
        raise ValueError(
            "finish_stage不接受该状态："
            f"{status.value}"
        )

    record = state.stages[
        stage_name.value
    ]

    if record.started_at is None:
        record.started_at = utc_now_iso()

    record.status = status
    record.completed_at = utc_now_iso()
    record.output_path = output_path
    record.message = message

    state.updated_at = utc_now_iso()


def complete_cycle(
    state: CycleState,
    *,
    status: CycleStatus,
    stop_reason: str | None = None,
) -> None:
    if status not in TERMINAL_CYCLE_STATUSES:
        raise ValueError(
            "complete_cycle只接受终止状态"
        )

    state.status = status
    state.current_step = StepName.COMPLETE
    state.stop_reason = stop_reason
    state.resume_allowed = False
    state.completed_at = utc_now_iso()
    state.updated_at = utc_now_iso()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WA Trader v2状态模型自检"
    )

    parser.add_argument(
        "--run-date",
        help="纽约日期YYYY-MM-DD",
    )

    parser.add_argument(
        "--cycle-id",
        help="已存在轮次ID",
    )

    parser.add_argument(
        "--initialize-daily",
        action="store_true",
        help="初始化daily_state.json",
    )

    parser.add_argument(
        "--initialize-cycle",
        action="store_true",
        help="初始化cycle_state.json",
    )

    parser.add_argument(
        "--cycle-kind",
        choices=[
            value.value
            for value in CycleKind
        ],
        default=CycleKind.DAILY_FULL.value,
    )

    parser.add_argument(
        "--no-review",
        "--no-need-review",
        "--no_need_review",
        dest="no_review",
        action="store_true",
    )
    parser.add_argument(
        "--allow-trade",
        "--allow_trade",
        dest="allow_trade",
        action="store_true",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已有状态文件",
    )

    arguments = parser.parse_args()

    print(f"脚本版本：{SCRIPT_VERSION}")

    try:
        run_date = normalize_run_date(
            arguments.run_date
        )

        daily_paths = build_daily_paths(
            run_date
        )

        if arguments.initialize_daily:
            daily_state = (
                initialize_daily_state(
                    daily_paths,
                    overwrite=arguments.overwrite,
                )
            )

            print("日期状态初始化成功")
            print(
                f"文件：{daily_paths.daily_state}"
            )
            print(
                "coarse_status："
                f"{daily_state.coarse_status.value}"
            )

        if arguments.initialize_cycle:
            if not arguments.cycle_id:
                raise ValueError(
                    "--initialize-cycle"
                    "必须同时提供--cycle-id"
                )

            cycle_paths = build_cycle_paths(
                cycle_id=arguments.cycle_id,
                run_date=run_date,
            )

            if not (
                cycle_paths.cycle_directory
                .exists()
            ):
                raise FileNotFoundError(
                    "轮次目录不存在，请先使用"
                    "runtime.py --create-cycle："
                    f"{cycle_paths.cycle_directory}"
                )

            review_mode = (
                ReviewMode.SKIPPED_BY_FLAG
                if arguments.no_review
                else ReviewMode.PROMPT
            )

            cycle_state = (
                initialize_cycle_state(
                    cycle_paths,
                    cycle_kind=CycleKind(
                        arguments.cycle_kind
                    ),
                    review_mode=review_mode,
                    overwrite=arguments.overwrite,
                    no_review=arguments.no_review,
                    allow_trade=(
                        arguments.allow_trade
                    ),
                )
            )

            print("轮次状态初始化成功")
            print(
                f"文件：{cycle_paths.cycle_state}"
            )
            print(
                "cycle_kind："
                f"{cycle_state.cycle_kind.value}"
            )
            print(
                "review_mode："
                f"{cycle_state.review_mode.value}"
            )
            print(
                "current_step："
                f"{cycle_state.current_step.value}"
            )

        if not (
            arguments.initialize_daily
            or arguments.initialize_cycle
        ):
            print(
                "未执行写入。使用"
                "--initialize-daily或"
                "--initialize-cycle进行自检。"
            )

        return 0

    except Exception as error:
        print("状态模型自检失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
