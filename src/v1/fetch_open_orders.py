from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from alpaca_client import create_trading_client
from config import get_project_root
from fetch_account import save_json_atomically


def serialize_value(value: Any) -> Any:
    """
    将 Alpaca 对象中的特殊类型转换为可写入 JSON 的值。

    支持：
    - datetime 和 date
    - UUID
    - 枚举
    - 普通字符串、数字、布尔值和 None
    """
    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    enum_value = getattr(value, "value", None)

    if enum_value is not None:
        return enum_value

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def build_order_data(
    order: Any,
    include_legs: bool = True,
) -> dict[str, Any]:
    """
    从 Alpaca 订单对象中提取当前项目需要的字段。

    include_legs 用于决定是否同时保存组合订单的子订单。
    """
    order_type = getattr(order, "type", None)

    if order_type is None:
        order_type = getattr(order, "order_type", None)

    result = {
        "id": serialize_value(getattr(order, "id", None)),
        "client_order_id": getattr(
            order,
            "client_order_id",
            None,
        ),
        "symbol": getattr(order, "symbol", None),
        "asset_class": serialize_value(
            getattr(order, "asset_class", None)
        ),
        "side": serialize_value(
            getattr(order, "side", None)
        ),
        "type": serialize_value(order_type),
        "order_class": serialize_value(
            getattr(order, "order_class", None)
        ),
        "time_in_force": serialize_value(
            getattr(order, "time_in_force", None)
        ),
        "qty": getattr(order, "qty", None),
        "notional": getattr(order, "notional", None),
        "filled_qty": getattr(order, "filled_qty", None),
        "filled_avg_price": getattr(
            order,
            "filled_avg_price",
            None,
        ),
        "limit_price": getattr(order, "limit_price", None),
        "stop_price": getattr(order, "stop_price", None),
        "trail_price": getattr(order, "trail_price", None),
        "trail_percent": getattr(
            order,
            "trail_percent",
            None,
        ),
        "status": serialize_value(
            getattr(order, "status", None)
        ),
        "extended_hours": getattr(
            order,
            "extended_hours",
            False,
        ),
        "created_at": serialize_value(
            getattr(order, "created_at", None)
        ),
        "updated_at": serialize_value(
            getattr(order, "updated_at", None)
        ),
        "submitted_at": serialize_value(
            getattr(order, "submitted_at", None)
        ),
        "filled_at": serialize_value(
            getattr(order, "filled_at", None)
        ),
        "expires_at": serialize_value(
            getattr(order, "expires_at", None)
        ),
        "canceled_at": serialize_value(
            getattr(order, "canceled_at", None)
        ),
        "failed_at": serialize_value(
            getattr(order, "failed_at", None)
        ),
        "replaced_at": serialize_value(
            getattr(order, "replaced_at", None)
        ),
        "replaced_by": serialize_value(
            getattr(order, "replaced_by", None)
        ),
        "replaces": serialize_value(
            getattr(order, "replaces", None)
        ),
        "position_intent": serialize_value(
            getattr(order, "position_intent", None)
        ),
    }

    if include_legs:
        legs = getattr(order, "legs", None) or []

        result["legs"] = [
            build_order_data(
                leg,
                include_legs=False,
            )
            for leg in legs
        ]

    return result


def fetch_and_save_open_orders() -> Path:
    """
    获取所有未完成订单并保存为 JSON。

    即使没有未完成订单，也会正常保存空数组。
    """
    trading_client = create_trading_client()

    request = GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        limit=500,
        nested=True,
    )

    orders = trading_client.get_orders(filter=request)

    order_data = [
        build_order_data(order)
        for order in orders
    ]

    order_data.sort(
        key=lambda item: item["submitted_at"] or "",
        reverse=True,
    )

    result = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "alpaca",
        "status": "success",
        "data": {
            "order_count": len(order_data),
            "orders": order_data,
        },
    }

    output_path = (
        get_project_root()
        / "data"
        / "snapshots"
        / "open_orders.json"
    )

    save_json_atomically(output_path, result)

    return output_path


def main() -> int:
    """单独运行本文件时，测试未完成订单接口。"""
    try:
        output_path = fetch_and_save_open_orders()

        print("Alpaca 未完成订单读取成功")
        print(f"保存位置：{output_path}")

        return 0

    except Exception as error:
        print("Alpaca 未完成订单读取失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())