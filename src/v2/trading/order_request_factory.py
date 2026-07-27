"""把已校验订单转换为精确的 Alpaca SDK 请求规格。

作用：生成可审计 JSON specs，并在本地实例化 MarketOrderRequest 或 LimitOrderRequest 校验参数。
重要性：本模块只构造对象，绝不持有 TradingClient，也不提供提交、取消、替换或平仓能力。
"""

from __future__ import annotations

from decimal import Decimal
from collections.abc import Mapping
from typing import Any

from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    TimeInForce,
)
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
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
) -> (
    MarketOrderRequest
    | LimitOrderRequest
    | StopOrderRequest
    | StopLimitOrderRequest
    | TrailingStopOrderRequest
):
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
        "order_class": OrderClass(
            order.order_class
        ),
    }
    if order.take_profit_limit_price is not None:
        common["take_profit"] = TakeProfitRequest(
            limit_price=(
                order.take_profit_limit_price
            )
        )
    if order.stop_loss_stop_price is not None:
        common["stop_loss"] = StopLossRequest(
            stop_price=order.stop_loss_stop_price,
            limit_price=order.stop_loss_limit_price,
        )
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
    if order.order_type == "stop":
        if order.stop_price is None:
            raise ValueError(
                "StopOrderRequest缺少stop_price"
            )
        return StopOrderRequest(
            **common,
            stop_price=order.stop_price,
        )
    if order.order_type == "stop_limit":
        if (
            order.stop_price is None
            or order.limit_price is None
        ):
            raise ValueError(
                "StopLimitOrderRequest缺少stop_price或limit_price"
            )
        return StopLimitOrderRequest(
            **common,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
        )
    if order.order_type == "trailing_stop":
        if (
            (order.trail_price is None)
            == (order.trail_percent is None)
        ):
            raise ValueError(
                "TrailingStopOrderRequest必须且只能设置trail_price或trail_percent"
            )
        return TrailingStopOrderRequest(
            **common,
            trail_price=order.trail_price,
            trail_percent=order.trail_percent,
        )
    raise ValueError(
        f"Stage F不支持的订单类型：{order.order_type}"
    )


def build_sdk_request_from_spec(
    spec: Mapping[str, Any],
) -> (
    MarketOrderRequest
    | LimitOrderRequest
    | StopOrderRequest
    | StopLimitOrderRequest
    | TrailingStopOrderRequest
):
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
        "order_class": OrderClass(
            str(spec.get("order_class", "simple"))
        ),
    }
    if (
        spec.get("take_profit_limit_price")
        is not None
    ):
        common["take_profit"] = TakeProfitRequest(
            limit_price=Decimal(
                str(
                    spec[
                        "take_profit_limit_price"
                    ]
                )
            )
        )
    if spec.get("stop_loss_stop_price") is not None:
        common["stop_loss"] = StopLossRequest(
            stop_price=Decimal(
                str(spec["stop_loss_stop_price"])
            ),
            limit_price=(
                Decimal(
                    str(
                        spec[
                            "stop_loss_limit_price"
                        ]
                    )
                )
                if spec.get(
                    "stop_loss_limit_price"
                )
                is not None
                else None
            ),
        )
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
    if request_class == "StopOrderRequest":
        if spec.get("stop_price") is None:
            raise ValueError(
                "StopOrderRequest缺少stop_price"
            )
        return StopOrderRequest(
            **common,
            stop_price=Decimal(
                str(spec["stop_price"])
            ),
        )
    if request_class == "StopLimitOrderRequest":
        if (
            spec.get("stop_price") is None
            or spec.get("limit_price") is None
        ):
            raise ValueError(
                "StopLimitOrderRequest缺少stop_price或limit_price"
            )
        return StopLimitOrderRequest(
            **common,
            stop_price=Decimal(
                str(spec["stop_price"])
            ),
            limit_price=Decimal(
                str(spec["limit_price"])
            ),
        )
    if request_class == "TrailingStopOrderRequest":
        trail_price = spec.get("trail_price")
        trail_percent = spec.get("trail_percent")
        if (
            (trail_price is None)
            == (trail_percent is None)
        ):
            raise ValueError(
                "TrailingStopOrderRequest必须且只能设置trail_price或trail_percent"
            )
        return TrailingStopOrderRequest(
            **common,
            trail_price=(
                Decimal(str(trail_price))
                if trail_price is not None
                else None
            ),
            trail_percent=(
                Decimal(str(trail_percent))
                if trail_percent is not None
                else None
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
                request_class={
                    "market": "MarketOrderRequest",
                    "limit": "LimitOrderRequest",
                    "stop": "StopOrderRequest",
                    "stop_limit": (
                        "StopLimitOrderRequest"
                    ),
                    "trailing_stop": (
                        "TrailingStopOrderRequest"
                    ),
                }[order.order_type],
                symbol=order.symbol,
                qty=order.quantity,
                side=order.side,
                time_in_force=order.time_in_force,
                limit_price=order.limit_price,
                order_class=order.order_class,
                stop_price=order.stop_price,
                trail_price=order.trail_price,
                trail_percent=order.trail_percent,
                take_profit_limit_price=(
                    order.take_profit_limit_price
                ),
                stop_loss_stop_price=(
                    order.stop_loss_stop_price
                ),
                stop_loss_limit_price=(
                    order.stop_loss_limit_price
                ),
                protection_role=(
                    order.protection_role
                ),
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
