"""执行 Stage G 唯一获准的 Alpaca paper 下单写操作。

作用：仅从 validated request spec 提交 approved 订单，并在写前查重、写前记账、异常后查询。
重要性：本文件是 submit_order 的唯一生产白名单；blind retry 永远为零，uncertain 会立即停止后续写。
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Callable
from typing import Any

from v2.data.alpaca_client import AlpacaClients
from v2.data.orders import normalize_order
from v2.models.submission import (
    SubmissionOperation,
    SubmissionOperationState,
    sanitized_error,
)
from v2.runtime import utc_now_iso
from v2.trading.order_request_factory import (
    build_sdk_request_from_spec,
)
from v2.trading.submission_journal import (
    SubmissionJournal,
)


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lookup_by_client_id(
    clients: AlpacaClients,
    client_order_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Return (order, definitely_absent); 404 is the only definite absence."""

    try:
        raw = clients.trading.get_order_by_client_id(
            client_order_id
        )
        return normalize_order(raw), False
    except Exception as error:
        return None, _status_code(error) == 404


def _record_order(
    journal: SubmissionJournal,
    operation: SubmissionOperation,
    order: Mapping[str, Any],
    *,
    state: SubmissionOperationState,
) -> SubmissionOperation:
    recorded = journal.transition(
        operation.operation_id,
        state,
        broker_order_id=str(
            order.get("broker_order_id") or ""
        )
        or None,
        broker_status=str(
            order.get("status") or ""
        ).lower()
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
    return recorded


def submit_approved_order(
    *,
    clients: AlpacaClients,
    spec: Mapping[str, Any],
    operation: SubmissionOperation,
    journal: SubmissionJournal,
    write_preflight: Callable[[], None] | None = None,
) -> SubmissionOperation:
    """Submit once, or recover by stable client_order_id without retry."""

    clients.validate()
    if operation.operation_type.value != "submit":
        raise ValueError("submitter只接受submit操作")
    if operation.state in {
        SubmissionOperationState.COMPLETED,
        SubmissionOperationState.RESPONSE_RECEIVED,
        SubmissionOperationState.LOOKUP_CONFIRMED,
        SubmissionOperationState.RECONCILED,
        SubmissionOperationState.SKIPPED,
        SubmissionOperationState.FAILED_DEFINITE,
    }:
        return operation
    if operation.state == SubmissionOperationState.UNCERTAIN:
        return operation
    client_order_id = str(spec["client_order_id"])
    if operation.client_order_id != client_order_id:
        raise ValueError("journal与request spec client_order_id不一致")
    if operation.plan_id != str(spec["plan_id"]):
        raise ValueError("journal与request spec plan_id不一致")

    existing, definitely_absent = _lookup_by_client_id(
        clients, client_order_id
    )
    if existing is not None:
        return _record_order(
            journal,
            operation,
            existing,
            state=SubmissionOperationState.LOOKUP_CONFIRMED,
        )
    if operation.state == SubmissionOperationState.REQUEST_STARTED:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.UNCERTAIN,
            error={
                "type": "RecoveryUncertain",
                "message": (
                    "写前journal显示请求已开始，但券商端无法确认；"
                    "禁止重复提交"
                ),
            },
        )
    if not definitely_absent:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.UNCERTAIN,
            error={
                "type": "LookupUnavailable",
                "message": "写前幂等查询失败，未执行券商写操作",
            },
        )

    try:
        if write_preflight is not None:
            write_preflight()
        request = build_sdk_request_from_spec(spec)
    except Exception as error:
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.FAILED_DEFINITE,
            error=sanitized_error(error),
        )

    journal.persist_before_write(operation.operation_id)
    try:
        raw = clients.trading.submit_order(
            order_data=request
        )
        normalized = normalize_order(raw)
        operation = _record_order(
            journal,
            operation,
            normalized,
            state=SubmissionOperationState.RESPONSE_RECEIVED,
        )
        confirmed, _ = _lookup_by_client_id(
            clients, client_order_id
        )
        if confirmed is not None:
            operation = _record_order(
                journal,
                operation,
                confirmed,
                state=SubmissionOperationState.COMPLETED,
            )
        return operation
    except Exception as error:
        found, absent_after_error = _lookup_by_client_id(
            clients, client_order_id
        )
        if found is not None:
            return _record_order(
                journal,
                operation,
                found,
                state=SubmissionOperationState.LOOKUP_CONFIRMED,
            )
        code = _status_code(error)
        if (
            code is not None
            and 400 <= code < 500
            and absent_after_error
        ):
            return journal.transition(
                operation.operation_id,
                SubmissionOperationState.FAILED_DEFINITE,
                error=sanitized_error(error),
            )
        return journal.transition(
            operation.operation_id,
            SubmissionOperationState.UNCERTAIN,
            error=sanitized_error(error)
            or {
                "type": "SubmissionUncertain",
                "message": "券商写结果无法确认",
            },
            last_checked_at=utc_now_iso(),
        )


def validated_approved_plan_ids(
    validated: Mapping[str, Any],
) -> set[str]:
    return {
        str(item["plan_id"])
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
        and item.get("status") == "approved"
        and item.get("plan_id")
    }


def request_specs_for_approved(
    validated: Mapping[str, Any],
    request_specs: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Fail closed unless every submitted spec belongs to validated approved."""

    approved = validated_approved_plan_ids(validated)
    specs = [
        item
        for item in request_specs.get("requests", [])
        if isinstance(item, Mapping)
    ]
    unexpected = {
        str(item.get("plan_id"))
        for item in specs
        if str(item.get("plan_id")) not in approved
    }
    spec_plan_ids = {
        str(item.get("plan_id"))
        for item in specs
    }
    missing = approved - spec_plan_ids
    if unexpected or missing:
        raise ValueError(
            "approved订单与request specs不一致："
            f"unexpected={','.join(sorted(unexpected)) or 'none'}；"
            f"missing={','.join(sorted(missing)) or 'none'}"
        )
    return specs
