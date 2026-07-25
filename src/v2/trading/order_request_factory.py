"""把已校验订单转换为精确的 Alpaca SDK 请求规格。

作用：生成可审计 JSON specs，并在本地实例化 MarketOrderRequest 或 LimitOrderRequest 校验参数。
重要性：本模块只构造对象，绝不持有 TradingClient，也不提供提交、取消、替换或平仓能力。
"""

from __future__ import annotations

from decimal import Decimal
from collections.abc import Mapping
from typing import Any

from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
)
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
)

from v2.models.orders import (
    BrokerRequestSpec,
    OrderStatus,
    ProposedOrder,
    ValidatedOrderPlan,
)


APPROVED_STATES = {
    OrderStatus.APPROVED,
    OrderStatus.DRY_RUN_APPROVED,
}


def build_sdk_request(
    order: ProposedOrder,
) -> MarketOrderRequest | LimitOrderRequest:
    """Locally validate one request with the installed official SDK model."""

    common: dict[str, Any] = {
        "symbol": order.symbol,
        "qty": order.quantity,
        "side": OrderSide(order.side),
        "time_in_force": TimeInForce(
            order.time_in_force
        ),
        "extended_hours": order.extended_hours,
        "client_order_id": order.client_order_id,
    }
    if order.order_type == "market":
        return MarketOrderRequest(**common)
    if order.order_type == "limit":
        if order.limit_price is None:
            raise ValueError(
                "LimitOrderRequest缺少limit_price"
            )
        return LimitOrderRequest(
            **common,
            limit_price=order.limit_price,
        )
    raise ValueError(
        f"Stage F不支持的订单类型：{order.order_type}"
    )


def build_sdk_request_from_spec(
    spec: Mapping[str, Any],
) -> MarketOrderRequest | LimitOrderRequest:
    """Rebuild only a locally validated persisted request specification."""

    if spec.get("local_sdk_validated") is not True:
        raise ValueError("request spec尚未通过本地SDK校验")
    common: dict[str, Any] = {
        "symbol": str(spec["symbol"]),
        "qty": Decimal(str(spec["qty"])),
        "side": OrderSide(str(spec["side"])),
        "time_in_force": TimeInForce(
            str(spec["time_in_force"])
        ),
        "extended_hours": bool(
            spec.get("extended_hours", False)
        ),
        "client_order_id": str(
            spec["client_order_id"]
        ),
    }
    request_class = str(spec["request_class"])
    if request_class == "MarketOrderRequest":
        if common["extended_hours"]:
            raise ValueError("market订单不能启用extended_hours")
        return MarketOrderRequest(**common)
    if request_class == "LimitOrderRequest":
        if spec.get("limit_price") is None:
            raise ValueError("LimitOrderRequest缺少limit_price")
        return LimitOrderRequest(
            **common,
            limit_price=Decimal(
                str(spec["limit_price"])
            ),
        )
    raise ValueError(
        f"不支持的request_class：{request_class}"
    )


def create_request_specs(
    plan: ValidatedOrderPlan,
) -> tuple[BrokerRequestSpec, ...]:
    """Create deterministic SDK-validated specs without a broker call."""

    specs: list[BrokerRequestSpec] = []
    for validated in plan.orders:
        if validated.status not in APPROVED_STATES:
            continue
        order = validated.order
        build_sdk_request(order)
        specs.append(
            BrokerRequestSpec(
                plan_id=order.plan_id,
                request_class=(
                    "MarketOrderRequest"
                    if order.order_type == "market"
                    else "LimitOrderRequest"
                ),
                symbol=order.symbol,
                qty=order.quantity,
                side=order.side,
                time_in_force=order.time_in_force,
                limit_price=order.limit_price,
                extended_hours=(
                    order.extended_hours
                ),
                client_order_id=(
                    order.client_order_id
                ),
                local_sdk_validated=True,
            )
        )
    return tuple(specs)


def request_specs_document(
    plan: ValidatedOrderPlan,
    specs: tuple[BrokerRequestSpec, ...],
) -> dict[str, Any]:
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
        "submission_requested": (
            plan.proposed.permission
            .submission_requested
        ),
        "submission_performed": False,
        "submitted_order_count": 0,
        "requests": [
            spec.to_dict() for spec in specs
        ],
    }
