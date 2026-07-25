"""对 Stage G paper 写结果执行即时、只读的券商事实对账。

作用：重新获取账户、持仓、open/today/tracked orders，分类 fill、partial、open、reject 与 uncertain。
重要性：本地提交响应不是最终事实；对账结果决定 cycle 终态以及下一轮是否必须重新平衡。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from v2.data.account import fetch_account
from v2.data.alpaca_client import AlpacaClients
from v2.data.orders import (
    fetch_open_orders,
    fetch_today_orders,
    normalize_order,
)
from v2.data.positions import fetch_positions
from v2.models.submission import (
    ACTIVE_ORDER_STATUSES,
    SubmissionOperation,
    SubmissionOperationState,
)
from v2.runtime import atomic_write_json, utc_now_iso
from v2.runtime import (
    build_cycle_paths,
    build_daily_paths,
    load_json_object,
    normalize_cycle_id,
    normalize_run_date,
)


def _tracked_order(
    clients: AlpacaClients,
    operation: SubmissionOperation,
) -> dict[str, Any] | None:
    try:
        if operation.broker_order_id:
            raw = clients.trading.get_order_by_id(
                operation.broker_order_id
            )
        elif operation.client_order_id:
            raw = clients.trading.get_order_by_client_id(
                operation.client_order_id
            )
        else:
            return None
        return normalize_order(raw)
    except Exception:
        return None


def reconcile_submission(
    *,
    clients: AlpacaClients,
    profile_id: str,
    cycle_id: str,
    operations: Iterable[SubmissionOperation],
    output_path: Any | None = None,
) -> dict[str, Any]:
    """Fetch one coherent post-write snapshot and persist it atomically."""

    clients.validate()
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    account: dict[str, Any] = {}
    positions: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    today_orders: list[dict[str, Any]] = []
    for label, loader, fallback in (
        ("account", lambda: fetch_account(clients), {}),
        ("positions", lambda: fetch_positions(clients), []),
        ("open_orders", lambda: fetch_open_orders(clients), []),
        ("today_orders", lambda: fetch_today_orders(clients), []),
    ):
        try:
            value = loader()
        except Exception as error:
            errors.append(
                {
                    "code": f"RECONCILIATION_{label.upper()}_FAILED",
                    "message": error.__class__.__name__,
                }
            )
            value = fallback
        if label == "account":
            account = value
        elif label == "positions":
            positions = value
        elif label == "open_orders":
            open_orders = value
        else:
            today_orders = value

    operation_list = list(operations)
    tracked_orders: list[dict[str, Any]] = []
    unresolved = 0
    for operation in operation_list:
        order = _tracked_order(clients, operation)
        if order is not None:
            tracked_orders.append(order)
        elif operation.state == SubmissionOperationState.UNCERTAIN:
            unresolved += 1
        elif operation.broker_order_id or operation.client_order_id:
            warnings.append(
                f"tracked order暂不可读取：{operation.operation_id}"
            )

    unique: dict[str, dict[str, Any]] = {}
    for order in tracked_orders:
        key = str(
            order.get("broker_order_id")
            or order.get("client_order_id")
        )
        unique[key] = order
    tracked_orders = list(unique.values())
    tracked_orders.sort(
        key=lambda item: (
            str(item.get("symbol", "")),
            str(item.get("broker_order_id", "")),
        )
    )
    statuses = [
        str(order.get("status", "")).lower()
        for order in tracked_orders
    ]
    summary = {
        "filled": statuses.count("filled"),
        "partially_filled": statuses.count("partially_filled"),
        "open": sum(
            status in ACTIVE_ORDER_STATUSES
            for status in statuses
        ),
        "canceled": statuses.count("canceled"),
        "rejected": statuses.count("rejected"),
        "expired": statuses.count("expired"),
        "uncertain": unresolved,
    }
    reasons: list[str] = []
    if summary["rejected"]:
        reasons.append("broker_order_rejected")
    if summary["canceled"] or summary["expired"]:
        reasons.append("capital_or_quantity_released")
    if summary["partially_filled"]:
        reasons.append("partial_fill_changed_exposure")
    if summary["filled"]:
        reasons.append("fill_changed_positions_and_capital")
    if summary["uncertain"]:
        reasons.append("submission_result_uncertain")
    payload = {
        "schema_version": "1.0",
        "profile_id": profile_id,
        "environment": "paper",
        "cycle_id": cycle_id,
        "reconciled_at": utc_now_iso(),
        "account": account,
        "positions": positions,
        "open_orders": open_orders,
        "today_orders": today_orders,
        "tracked_orders": tracked_orders,
        "summary": summary,
        "capital": {
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "portfolio_value": account.get("portfolio_value"),
            "equity": account.get("equity"),
        },
        "requires_next_cycle_rebalance": bool(reasons),
        "reasons": reasons,
        "errors": errors,
        "warnings": warnings,
    }
    if output_path is not None:
        atomic_write_json(output_path, payload)
    return payload


def maintain_previous_submissions(
    *,
    clients: AlpacaClients,
    project_root: Any,
    run_date: str,
    profile_id: str,
    strategy_id: str,
    strategy_version: str,
) -> list[str]:
    """Reconcile prior cycles with broker operations before a new cycle starts."""

    maintained: list[str] = []
    daily = build_daily_paths(
        run_date,
        project_root=project_root,
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )
    day_directories = []
    if daily.identity_root.is_dir():
        for child in daily.identity_root.iterdir():
            if not child.is_dir():
                continue
            try:
                normalized_day = normalize_run_date(
                    child.name
                )
            except ValueError:
                continue
            if (
                normalized_day == child.name
                and normalized_day <= run_date
            ):
                day_directories.append(child)
    cycle_locations: list[tuple[str, str]] = []
    for day_directory in sorted(day_directories):
        cycles_directory = day_directory / "cycles"
        if not cycles_directory.is_dir():
            continue
        for child in sorted(cycles_directory.iterdir()):
            if not child.is_dir():
                continue
            try:
                normalize_cycle_id(child.name)
            except ValueError:
                continue
            cycle_locations.append(
                (day_directory.name, child.name)
            )
    for cycle_run_date, cycle_id in cycle_locations:
        paths = build_cycle_paths(
            cycle_id=cycle_id,
            run_date=cycle_run_date,
            project_root=project_root,
            profile_id=profile_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )
        if not paths.submission_journal.is_file():
            continue
        payload = load_json_object(
            paths.submission_journal
        )
        operations = [
            SubmissionOperation.from_dict(item)
            for item in payload.get("operations", [])
            if isinstance(item, dict)
        ]
        needs_maintenance = any(
            operation.state
            in {
                SubmissionOperationState.REQUEST_STARTED,
                SubmissionOperationState.RESPONSE_RECEIVED,
                SubmissionOperationState.LOOKUP_CONFIRMED,
                SubmissionOperationState.UNCERTAIN,
            }
            or operation.broker_status
            in ACTIVE_ORDER_STATUSES
            for operation in operations
        )
        if not needs_maintenance:
            continue
        reconcile_submission(
            clients=clients,
            profile_id=profile_id,
            cycle_id=cycle_id,
            operations=operations,
            output_path=paths.reconciliation,
        )
        maintained.append(cycle_id)
    return maintained
