"""定义 Stage G Paper/Live 提交、恢复和对账的持久化模型。

作用：为 submission intent、逐操作 journal、券商结果及状态分类提供严格数据合同。
重要性：这些模型是幂等恢复的事实来源；没有写前记录或出现 uncertain 时绝不能盲目重试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any, Mapping

from v2.runtime import utc_now_iso


class SubmissionOperationState(StrEnum):
    PREPARED = "prepared"
    REQUEST_STARTED = "request_started"
    RESPONSE_RECEIVED = "response_received"
    LOOKUP_CONFIRMED = "lookup_confirmed"
    RECONCILED = "reconciled"
    COMPLETED = "completed"
    FAILED_DEFINITE = "failed_definite"
    UNCERTAIN = "uncertain"
    SKIPPED = "skipped"


class SubmissionOperationType(StrEnum):
    SUBMIT = "submit"
    CANCEL = "cancel"


BROKER_ORDER_STATUSES = frozenset(
    {
        "accepted",
        "pending_new",
        "new",
        "partially_filled",
        "filled",
        "done_for_day",
        "canceled",
        "expired",
        "replaced",
        "pending_cancel",
        "pending_replace",
        "rejected",
        "suspended",
        "calculated",
        "accepted_for_bidding",
        "stopped",
    }
)

ACTIVE_ORDER_STATUSES = frozenset(
    {
        "accepted",
        "pending_new",
        "new",
        "partially_filled",
        "done_for_day",
        "pending_cancel",
        "pending_replace",
        "accepted_for_bidding",
        "stopped",
        "suspended",
    }
)

TERMINAL_ORDER_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "expired",
        "rejected",
        "replaced",
    }
)


def sanitized_error(error: BaseException | None) -> dict[str, str] | None:
    """Return a secret-free, bounded error description."""

    if error is None:
        return None
    message = str(error).replace("\n", " ").strip()
    message = re.sub(
        (
            r"(?i)\b(api[-_ ]?key|secret[-_ ]?key|"
            r"authorization|bearer)\b\s*[:=]?\s*\S+"
        ),
        r"\1=[REDACTED]",
        message,
    )
    return {
        "type": error.__class__.__name__,
        "message": message[:500] or "broker operation failed",
    }


@dataclass
class SubmissionOperation:
    operation_id: str
    operation_type: SubmissionOperationType
    plan_id: str | None
    client_order_id: str | None
    broker_order_id: str | None
    symbol: str
    state: SubmissionOperationState
    attempt_count: int = 0
    prepared_at: str = field(default_factory=utc_now_iso)
    request_started_at: str | None = None
    response_received_at: str | None = None
    last_checked_at: str | None = None
    completed_at: str | None = None
    broker_status: str | None = None
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    request_summary: dict[str, Any] = field(default_factory=dict)
    error: dict[str, str] | None = None
    dependency_operation_ids: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.operation_id or not self.symbol:
            raise ValueError("submission operation身份不完整")
        if self.attempt_count < 0:
            raise ValueError("attempt_count不能小于0")
        if (
            self.broker_status is not None
            and self.broker_status not in BROKER_ORDER_STATUSES
        ):
            raise ValueError(
                f"未知Alpaca订单状态：{self.broker_status}"
            )
        if self.state == SubmissionOperationState.UNCERTAIN and not self.error:
            raise ValueError("uncertain操作必须记录原因")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type.value,
            "plan_id": self.plan_id,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "prepared_at": self.prepared_at,
            "request_started_at": self.request_started_at,
            "response_received_at": self.response_received_at,
            "last_checked_at": self.last_checked_at,
            "completed_at": self.completed_at,
            "broker_status": self.broker_status,
            "filled_quantity": self.filled_quantity,
            "average_fill_price": self.average_fill_price,
            "request_summary": dict(self.request_summary),
            "error": dict(self.error) if self.error else None,
            "dependency_operation_ids": list(
                self.dependency_operation_ids
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SubmissionOperation":
        operation = cls(
            operation_id=str(payload["operation_id"]),
            operation_type=SubmissionOperationType(
                payload["operation_type"]
            ),
            plan_id=(
                str(payload["plan_id"])
                if payload.get("plan_id") is not None
                else None
            ),
            client_order_id=(
                str(payload["client_order_id"])
                if payload.get("client_order_id") is not None
                else None
            ),
            broker_order_id=(
                str(payload["broker_order_id"])
                if payload.get("broker_order_id") is not None
                else None
            ),
            symbol=str(payload["symbol"]),
            state=SubmissionOperationState(payload["state"]),
            attempt_count=int(payload.get("attempt_count", 0)),
            prepared_at=str(payload.get("prepared_at", utc_now_iso())),
            request_started_at=payload.get("request_started_at"),
            response_received_at=payload.get("response_received_at"),
            last_checked_at=payload.get("last_checked_at"),
            completed_at=payload.get("completed_at"),
            broker_status=(
                str(payload["broker_status"]).lower()
                if payload.get("broker_status")
                else None
            ),
            filled_quantity=float(payload.get("filled_quantity", 0)),
            average_fill_price=(
                float(payload["average_fill_price"])
                if payload.get("average_fill_price") is not None
                else None
            ),
            request_summary=dict(payload.get("request_summary", {})),
            error=(
                dict(payload["error"])
                if isinstance(payload.get("error"), Mapping)
                else None
            ),
            dependency_operation_ids=[
                str(item)
                for item in payload.get(
                    "dependency_operation_ids", []
                )
            ],
        )
        operation.validate()
        return operation


@dataclass(frozen=True)
class SubmissionIntent:
    profile_id: str
    environment: str
    run_date: str
    cycle_id: str
    allow_trade: bool
    validated_orders_hash: str
    request_specs_hash: str
    action_plan_hash: str
    submission_policy: str
    submission_policy_hash: str
    approved_plan_ids: tuple[str, ...]
    dependent_plan_ids: tuple[str, ...]
    cancel_action_ids: tuple[str, ...]
    expected_write_count: int
    intent_revision: int = 1
    prior_revisions: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=utc_now_iso)
    status: str = "prepared"

    def to_dict(self) -> dict[str, Any]:
        if (
            self.environment not in {"paper", "live"}
            or self.expected_write_count < 0
            or self.intent_revision < 1
        ):
            raise ValueError("submission intent环境或写入数量无效")
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "environment": self.environment,
            "run_date": self.run_date,
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "allow_trade": self.allow_trade,
            "validated_orders_hash": self.validated_orders_hash,
            "request_specs_hash": self.request_specs_hash,
            "action_plan_hash": self.action_plan_hash,
            "submission_policy": self.submission_policy,
            "submission_policy_hash": self.submission_policy_hash,
            "approved_plan_ids": list(self.approved_plan_ids),
            "dependent_plan_ids": list(self.dependent_plan_ids),
            "cancel_action_ids": list(self.cancel_action_ids),
            "expected_write_count": self.expected_write_count,
            "intent_revision": self.intent_revision,
            "prior_revisions": [
                dict(item) for item in self.prior_revisions
            ],
            "status": self.status,
        }


def broker_submission_document(
    *,
    profile_id: str,
    environment: str = "paper",
    run_date: str,
    cycle_id: str,
    submission_requested: bool,
    submission_performed: bool,
    validated_orders_hash: str,
    operations: list[SubmissionOperation],
    started_at: str,
    completed_at: str | None = None,
    global_errors: list[dict[str, Any]] | None = None,
    global_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the canonical, secret-free result document."""

    if environment not in {"paper", "live"}:
        raise ValueError("broker submission环境无效")
    submitted = sum(
        operation.operation_type == SubmissionOperationType.SUBMIT
        and operation.attempt_count > 0
        and operation.state
        in {
            SubmissionOperationState.RESPONSE_RECEIVED,
            SubmissionOperationState.LOOKUP_CONFIRMED,
            SubmissionOperationState.RECONCILED,
            SubmissionOperationState.COMPLETED,
        }
        for operation in operations
    )
    return {
        "schema_version": "1.0",
        "profile_id": profile_id,
        "environment": environment,
        "run_date": run_date,
        "cycle_id": cycle_id,
        "started_at": started_at,
        "completed_at": completed_at or utc_now_iso(),
        "submission_requested": submission_requested,
        "submission_performed": submission_performed,
        "validated_orders_hash": validated_orders_hash,
        "submitted_count": submitted,
        "existing_count": sum(
            operation.state
            == SubmissionOperationState.LOOKUP_CONFIRMED
            and operation.attempt_count == 0
            for operation in operations
        ),
        "rejected_count": sum(
            operation.broker_status == "rejected"
            or operation.state
            == SubmissionOperationState.FAILED_DEFINITE
            for operation in operations
        ),
        "uncertain_count": sum(
            operation.state == SubmissionOperationState.UNCERTAIN
            for operation in operations
        ),
        "cancel_requested_count": sum(
            operation.operation_type == SubmissionOperationType.CANCEL
            and operation.request_started_at is not None
            for operation in operations
        ),
        "cancel_confirmed_count": sum(
            operation.operation_type == SubmissionOperationType.CANCEL
            and operation.broker_status in TERMINAL_ORDER_STATUSES
            for operation in operations
        ),
        "operations": [operation.to_dict() for operation in operations],
        "global_errors": list(global_errors or []),
        "global_warnings": list(global_warnings or []),
    }
