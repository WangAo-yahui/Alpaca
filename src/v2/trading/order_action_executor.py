"""执行 Stage G 唯一获准的 Alpaca paper 取消写操作。

作用：先读取最新订单，再写前记账、请求取消并轮询到明确终态。
重要性：本文件是 cancel_order_by_id 的唯一生产白名单；pending_cancel、部分成交或未知结果绝不解锁 replacement。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from v2.data.alpaca_client import AlpacaClients
from v2.data.orders import normalize_order
from v2.models.submission import (
    SubmissionOperation,
    SubmissionOperationState,
    TERMINAL_ORDER_STATUSES,
    sanitized_error,
)
from v2.trading.submission_journal import SubmissionJournal


def _read_order(
    clients: AlpacaClients,
    broker_order_id: str,
) -> dict[str, Any] | None:
    try:
        return normalize_order(
            clients.trading.get_order_by_id(
                broker_order_id
            )
        )
    except Exception:
        return None


def _apply_order(
    journal: SubmissionJournal,
    operation: SubmissionOperation,
    order: dict[str, Any],
    state: SubmissionOperationState,
) -> SubmissionOperation:
    return journal.transition(
        operation.operation_id,
        state,
        broker_order_id=str(
            order.get("broker_order_id") or ""
        )
        or operation.broker_order_id,
        broker_status=str(order.get("status") or "").lower()
        or None,
        filled_quantity=float(
            order.get("filled_quantity") or 0
        ),
        average_fill_price=(
            float(order["average_fill_price"])
            if order.get("average_fill_price") is not None
            else None
        ),
        error=None,
    )


def execute_cancel(
    *,
    clients: AlpacaClients,
    operation: SubmissionOperation,
    journal: SubmissionJournal,
    maximum_seconds: float = 10,
    interval_seconds: float = 1,
    sleeper: Callable[[float], None] = time.sleep,
) -> SubmissionOperation:
    """Cancel sequentially and require a terminal read before returning success."""

    clients.validate()
    if operation.operation_type.value != "cancel":
        raise ValueError("action executor只接受cancel操作")
    if operation.state in {
        SubmissionOperationState.COMPLETED,
        SubmissionOperationState.SKIPPED,
        SubmissionOperationState.FAILED_DEFINITE,
        SubmissionOperationState.UNCERTAIN,
    }:
        return operation
    broker_order_id = operation.broker_order_id
    if not broker_order_id:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.FAILED_DEFINITE,
            error={
                "type": "MissingBrokerOrderId",
                "message": "取消操作缺少broker_order_id",
            },
        )

    latest = _read_order(clients, broker_order_id)
    if latest is None:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.UNCERTAIN,
            error={
                "type": "CancelTargetLookupFailed",
                "message": "无法确认取消目标状态，未执行取消",
            },
        )
    if latest["status"] in TERMINAL_ORDER_STATUSES:
        return _apply_order(
            journal,
            operation,
            latest,
            SubmissionOperationState.SKIPPED,
        )
    if operation.state == SubmissionOperationState.REQUEST_STARTED:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.UNCERTAIN,
            error={
                "type": "CancelRecoveryUncertain",
                "message": "取消请求曾开始但尚无终态，禁止重复取消",
            },
        )
    already_requested = operation.state in {
        SubmissionOperationState.RESPONSE_RECEIVED,
        SubmissionOperationState.LOOKUP_CONFIRMED,
        SubmissionOperationState.RECONCILED,
    }
    if not already_requested:
        journal.persist_before_write(operation.operation_id)
        try:
            clients.trading.cancel_order_by_id(
                broker_order_id
            )
            journal.transition(
                operation.operation_id,
                SubmissionOperationState.RESPONSE_RECEIVED,
            )
        except Exception as error:
            latest = _read_order(clients, broker_order_id)
            if (
                latest is not None
                and latest["status"]
                in TERMINAL_ORDER_STATUSES
            ):
                return _apply_order(
                    journal,
                    operation,
                    latest,
                    SubmissionOperationState.COMPLETED,
                )
            return journal.transition(
                operation.operation_id,
                SubmissionOperationState.UNCERTAIN,
                error=sanitized_error(error),
            )

    elapsed = 0.0
    while elapsed <= maximum_seconds:
        latest = _read_order(clients, broker_order_id)
        if (
            latest is not None
            and latest["status"] in TERMINAL_ORDER_STATUSES
        ):
            return _apply_order(
                journal,
                operation,
                latest,
                SubmissionOperationState.COMPLETED,
            )
        if elapsed >= maximum_seconds:
            break
        sleeper(interval_seconds)
        elapsed += interval_seconds

    return journal.transition(
        operation.operation_id,
        SubmissionOperationState.UNCERTAIN,
        broker_status=(
            str(latest.get("status"))
            if latest is not None
            else None
        ),
        error={
            "type": "CancelNotConfirmed",
            "message": "取消未在短轮询窗口内进入终态",
        },
    )


def replacement_is_unlocked(
    cancel_operation: SubmissionOperation,
) -> bool:
    """Only a non-fill terminal cancellation may unlock fresh revalidation."""

    return (
        cancel_operation.state
        in {
            SubmissionOperationState.COMPLETED,
            SubmissionOperationState.SKIPPED,
        }
        and cancel_operation.broker_status
        in {"canceled", "expired", "rejected"}
    )
