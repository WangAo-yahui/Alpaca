"""提供 Stage G submit、cancel、timeout 与恢复测试使用的确定性 fake broker。

作用：精确记录写调用并模拟 404、明确拒绝、响应丢失、取消竞态和订单状态变化。
重要性：所有 paper 写安全测试必须离线完成，绝不能使用真实凭据或 Alpaca 账户。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from v2.data.alpaca_client import AlpacaClients
from v2.models.submission import (
    SubmissionOperation,
    SubmissionOperationState,
    SubmissionOperationType,
)
from v2.trading.submission_journal import SubmissionJournal
from tests.v2.fakes import (
    FakeStockDataClient,
    fake_account,
    fake_order,
)


class FakeAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class WriteTradingClient:
    def __init__(
        self,
        *,
        submit_error: BaseException | None = None,
        submit_accepts_before_error: bool = False,
        cancel_error: BaseException | None = None,
        cancel_statuses: list[str] | None = None,
        account: object | None = None,
    ) -> None:
        self.submit_error = submit_error
        self.submit_accepts_before_error = (
            submit_accepts_before_error
        )
        self.cancel_error = cancel_error
        self.cancel_statuses = list(
            cancel_statuses or ["canceled"]
        )
        self.account = account or fake_account()
        self.orders_by_client: dict[str, object] = {}
        self.orders_by_id: dict[str, object] = {}
        self.submit_calls = 0
        self.cancel_calls = 0
        self.get_by_client_calls = 0
        self.get_by_id_calls = 0

    def add_order(self, order: object) -> None:
        self.orders_by_client[
            str(getattr(order, "client_order_id"))
        ] = order
        self.orders_by_id[
            str(getattr(order, "id"))
        ] = order

    def get_order_by_client_id(
        self,
        client_id: str,
    ) -> object:
        self.get_by_client_calls += 1
        if client_id not in self.orders_by_client:
            raise FakeAPIError(
                "not found", status_code=404
            )
        return self.orders_by_client[client_id]

    def get_order_by_id(
        self,
        order_id: str,
        filter: object | None = None,
    ) -> object:
        del filter
        self.get_by_id_calls += 1
        if order_id not in self.orders_by_id:
            raise FakeAPIError(
                "not found", status_code=404
            )
        order = self.orders_by_id[order_id]
        if self.cancel_calls and self.cancel_statuses:
            setattr(
                order,
                "status",
                self.cancel_statuses.pop(0),
            )
        return order

    def submit_order(self, *, order_data: object) -> object:
        self.submit_calls += 1
        client_id = str(
            getattr(order_data, "client_order_id")
        )
        symbol = str(getattr(order_data, "symbol"))
        order = fake_order(
            symbol,
            id=f"broker-{self.submit_calls}",
            client_order_id=client_id,
            side=getattr(
                getattr(order_data, "side"),
                "value",
                getattr(order_data, "side"),
            ),
            qty=str(getattr(order_data, "qty")),
            filled_qty="0",
            filled_avg_price=None,
            status="new",
            extended_hours=bool(
                getattr(order_data, "extended_hours")
            ),
        )
        if self.submit_accepts_before_error:
            self.add_order(order)
        if self.submit_error is not None:
            raise self.submit_error
        self.add_order(order)
        return order

    def cancel_order_by_id(self, order_id: str) -> None:
        self.cancel_calls += 1
        if self.cancel_error is not None:
            raise self.cancel_error
        if order_id not in self.orders_by_id:
            raise FakeAPIError(
                "not found", status_code=404
            )

    def get_account(self) -> object:
        return self.account

    def get_all_positions(self) -> list[object]:
        return []

    def get_orders(self, *, filter: object) -> list[object]:
        status = getattr(
            getattr(filter, "status", None),
            "value",
            getattr(filter, "status", None),
        )
        orders = list(self.orders_by_id.values())
        if status == "open":
            return [
                order
                for order in orders
                if getattr(order, "status", "")
                not in {
                    "filled",
                    "canceled",
                    "expired",
                    "rejected",
                    "replaced",
                }
            ]
        return orders


def clients_for(
    trading: WriteTradingClient,
) -> AlpacaClients:
    return AlpacaClients(
        trading=trading,
        stock_data=FakeStockDataClient(),
        paper=True,
    )


def request_spec(
    *,
    plan_id: str = "plan-1",
    client_order_id: str = "wa2-paper1-test-1",
) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "request_class": "LimitOrderRequest",
        "symbol": "MU",
        "qty": "1",
        "side": "buy",
        "time_in_force": "day",
        "limit_price": "100",
        "extended_hours": False,
        "client_order_id": client_order_id,
        "local_sdk_validated": True,
    }


def operation(
    *,
    kind: SubmissionOperationType = (
        SubmissionOperationType.SUBMIT
    ),
    state: SubmissionOperationState = (
        SubmissionOperationState.PREPARED
    ),
    broker_order_id: str | None = None,
) -> SubmissionOperation:
    return SubmissionOperation(
        operation_id=(
            "submit-plan-1"
            if kind == SubmissionOperationType.SUBMIT
            else "cancel-action-1"
        ),
        operation_type=kind,
        plan_id=(
            "plan-1"
            if kind == SubmissionOperationType.SUBMIT
            else None
        ),
        client_order_id=(
            "wa2-paper1-test-1"
            if kind == SubmissionOperationType.SUBMIT
            else None
        ),
        broker_order_id=broker_order_id,
        symbol="MU",
        state=state,
    )


def journal_for(
    root: Path,
    operation_value: SubmissionOperation,
) -> SubmissionJournal:
    return SubmissionJournal.load_or_create(
        root / "submission_journal.json",
        profile_id="paper1",
        run_date="2026-07-24",
        cycle_id="20260724T140000",
        operations=[operation_value],
    )
