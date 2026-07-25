import argparse
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from alpaca_client import create_trading_client
from config import get_alpaca_credentials, get_project_root
from fetch_account import save_json_atomically
from fetch_open_orders import build_order_data, serialize_value


NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

STRATEGY_ORDER_PREFIX = "wa_v1_"

MAX_ORDER_RESULTS = 500
FILL_PAGE_SIZE = 100
MAX_FILL_PAGES = 100


def safe_float(value: Any) -> float | None:
    """将字符串或数字安全转换为有限浮点数。"""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def parse_market_date(value: str | None) -> date:
    """
    解析指定的纽约交易日期。

    没有传入日期时，使用当前纽约日期。
    """
    if value is None:
        return datetime.now(
            NEW_YORK_TIMEZONE
        ).date()

    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            "日期格式必须为 YYYY-MM-DD"
        ) from error


def get_market_day_range(
    market_date: date,
) -> tuple[datetime, datetime]:
    """
    获取纽约交易日期对应的完整自然日范围。

    这里用于查询当日提交的全部订单。
    """
    start_time = datetime.combine(
        market_date,
        time.min,
        tzinfo=NEW_YORK_TIMEZONE,
    )

    end_time = start_time + timedelta(days=1)

    return start_time, end_time


def build_today_order_data(
    order: Any,
) -> dict[str, Any]:
    """
    将 Alpaca 订单转换成日报所需的数据。

    在现有订单字段基础上，增加：
    - 剩余未成交数量
    - 是否属于本策略
    - 订单过期时间
    - trailing stop 的最高水位
    """
    result = build_order_data(order)

    result["expired_at"] = serialize_value(
        getattr(order, "expired_at", None)
    )

    result["hwm"] = getattr(
        order,
        "hwm",
        None,
    )

    quantity = safe_float(result.get("qty"))
    filled_quantity = safe_float(
        result.get("filled_qty")
    )

    remaining_quantity: float | None = None

    if (
        quantity is not None
        and filled_quantity is not None
    ):
        remaining_quantity = max(
            quantity - filled_quantity,
            0.0,
        )

    result["remaining_qty"] = (
        round(remaining_quantity, 8)
        if remaining_quantity is not None
        else None
    )

    client_order_id = result.get(
        "client_order_id"
    )

    result["is_strategy_order"] = (
        isinstance(client_order_id, str)
        and client_order_id.startswith(
            STRATEGY_ORDER_PREFIX
        )
    )

    return result


def fetch_orders_submitted_on_date(
    market_date: date,
) -> list[dict[str, Any]]:
    """
    查询纽约指定日期内提交的全部订单。

    包括：
    - 未完成
    - 已成交
    - 已取消
    - 已拒绝
    - 已过期
    """
    start_time, end_time = (
        get_market_day_range(market_date)
    )

    trading_client = create_trading_client()

    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=start_time,
        until=end_time,
        limit=MAX_ORDER_RESULTS,
        nested=True,
    )

    orders = trading_client.get_orders(
        filter=request
    )

    order_data = [
        build_today_order_data(order)
        for order in orders
    ]

    order_data.sort(
        key=lambda item: (
            item.get("submitted_at") or ""
        )
    )

    return order_data


def request_json(
    url: str,
    api_key: str,
    secret_key: str,
) -> Any:
    """向 Alpaca REST API 发起只读 GET 请求。"""
    request = Request(
        url=url,
        method="GET",
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
            "User-Agent": "wa-trader-v1",
        },
    )

    try:
        with urlopen(
            request,
            timeout=30,
        ) as response:
            response_text = response.read().decode(
                "utf-8"
            )

    except HTTPError as error:
        try:
            error_body = error.read().decode(
                "utf-8"
            )
        except Exception:
            error_body = ""

        raise RuntimeError(
            "Alpaca账户活动请求失败："
            f"HTTP {error.code} {error_body}"
        ) from error

    except URLError as error:
        raise RuntimeError(
            "无法连接Alpaca账户活动接口："
            f"{error.reason}"
        ) from error

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Alpaca账户活动接口返回了无效JSON"
        ) from error


def fetch_fill_activities(
    market_date: date,
) -> list[dict[str, Any]]:
    """
    查询指定纽约交易日期的真实成交活动。

    FILL活动同时包含：
    - 完全成交
    - 部分成交

    与订单提交日期不同，这里记录的是实际成交。
    """
    api_key, secret_key, paper = (
        get_alpaca_credentials()
    )

    if paper:
        base_url = (
            "https://paper-api.alpaca.markets"
        )
    else:
        base_url = "https://api.alpaca.markets"

    endpoint = (
        f"{base_url}/v2/account/activities/FILL"
    )

    activities: list[dict[str, Any]] = []

    page_token: str | None = None
    used_tokens: set[str] = set()

    for _ in range(MAX_FILL_PAGES):
        query_parameters = {
            "date": market_date.isoformat(),
            "direction": "asc",
            "page_size": FILL_PAGE_SIZE,
        }

        if page_token is not None:
            query_parameters["page_token"] = (
                page_token
            )

        url = (
            endpoint
            + "?"
            + urlencode(query_parameters)
        )

        response_data = request_json(
            url=url,
            api_key=api_key,
            secret_key=secret_key,
        )

        if not isinstance(response_data, list):
            raise RuntimeError(
                "Alpaca成交活动响应必须是数组"
            )

        page_activities = [
            item
            for item in response_data
            if isinstance(item, dict)
        ]

        activities.extend(page_activities)

        if len(page_activities) < FILL_PAGE_SIZE:
            break

        next_page_token = page_activities[-1].get(
            "id"
        )

        if (
            not isinstance(next_page_token, str)
            or not next_page_token
        ):
            break

        if next_page_token in used_tokens:
            raise RuntimeError(
                "Alpaca成交活动分页令牌重复"
            )

        used_tokens.add(next_page_token)
        page_token = next_page_token

    else:
        raise RuntimeError(
            "成交活动分页超过安全上限"
        )

    activities.sort(
        key=lambda item: (
            str(item.get("transaction_time", ""))
        )
    )

    return activities


def build_order_lookup(
    orders: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """根据 Alpaca 订单 ID 创建订单索引。"""
    lookup: dict[str, dict[str, Any]] = {}

    for order in orders:
        order_id = order.get("id")

        if isinstance(order_id, str) and order_id:
            lookup[order_id] = order

    return lookup


def fetch_missing_fill_orders(
    fill_activities: list[dict[str, Any]],
    order_lookup: dict[str, dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    """
    补充查询今天成交、但不是今天提交的订单。

    典型情况：
    昨天提交的 GTC 订单今天成交。
    """
    trading_client = create_trading_client()

    related_orders: list[dict[str, Any]] = []
    warnings: list[str] = []

    missing_order_ids = {
        str(activity.get("order_id"))
        for activity in fill_activities
        if activity.get("order_id")
        and str(activity.get("order_id"))
        not in order_lookup
    }

    for order_id in sorted(missing_order_ids):
        try:
            order = trading_client.get_order_by_id(
                order_id
            )

            order_data = build_today_order_data(
                order
            )

            order_lookup[order_id] = order_data
            related_orders.append(order_data)

        except Exception as error:
            warnings.append(
                f"无法读取成交对应订单 "
                f"{order_id}：{error}"
            )

    related_orders.sort(
        key=lambda item: (
            item.get("submitted_at") or ""
        )
    )

    return related_orders, warnings


def normalize_fill_activity(
    activity: dict[str, Any],
    order_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """整理单条真实成交记录。"""
    order_id_value = activity.get("order_id")

    order_id = (
        str(order_id_value)
        if order_id_value is not None
        else None
    )

    related_order = (
        order_lookup.get(order_id)
        if order_id is not None
        else None
    )

    client_order_id = (
        related_order.get("client_order_id")
        if related_order
        else None
    )

    return {
        "activity_id": activity.get("id"),
        "activity_type": activity.get(
            "activity_type"
        ),
        "fill_type": activity.get("type"),
        "transaction_time": activity.get(
            "transaction_time"
        ),
        "symbol": activity.get("symbol"),
        "side": activity.get("side"),
        "qty": activity.get("qty"),
        "price": activity.get("price"),
        "cum_qty": activity.get("cum_qty"),
        "leaves_qty": activity.get(
            "leaves_qty"
        ),
        "order_id": order_id,
        "client_order_id": client_order_id,
        "is_strategy_order": (
            isinstance(client_order_id, str)
            and client_order_id.startswith(
                STRATEGY_ORDER_PREFIX
            )
        ),
    }


def summarize_order_statuses(
    orders: list[dict[str, Any]],
) -> dict[str, int]:
    """统计各种订单状态的数量。"""
    status_counts: dict[str, int] = {}

    for order in orders:
        status = order.get("status")

        status_name = (
            str(status)
            if status is not None
            else "unknown"
        )

        status_counts[status_name] = (
            status_counts.get(status_name, 0)
            + 1
        )

    return dict(sorted(status_counts.items()))


def summarize_fills(
    fills: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    汇总当天真实成交。

    分别统计买入、卖出数量和成交金额。
    """
    symbol_data: dict[str, dict[str, Any]] = {}

    total_buy_notional = 0.0
    total_sell_notional = 0.0

    for fill in fills:
        symbol = fill.get("symbol")
        side = fill.get("side")

        quantity = safe_float(fill.get("qty"))
        price = safe_float(fill.get("price"))

        if (
            not isinstance(symbol, str)
            or side not in {"buy", "sell"}
            or quantity is None
            or price is None
        ):
            continue

        notional = quantity * price

        if symbol not in symbol_data:
            symbol_data[symbol] = {
                "symbol": symbol,
                "buy_qty": 0.0,
                "buy_notional": 0.0,
                "buy_avg_price": None,
                "sell_qty": 0.0,
                "sell_notional": 0.0,
                "sell_avg_price": None,
            }

        entry = symbol_data[symbol]

        if side == "buy":
            entry["buy_qty"] += quantity
            entry["buy_notional"] += notional
            total_buy_notional += notional
        else:
            entry["sell_qty"] += quantity
            entry["sell_notional"] += notional
            total_sell_notional += notional

    for entry in symbol_data.values():
        buy_quantity = entry["buy_qty"]
        sell_quantity = entry["sell_qty"]

        if buy_quantity > 0:
            entry["buy_avg_price"] = round(
                entry["buy_notional"]
                / buy_quantity,
                6,
            )

        if sell_quantity > 0:
            entry["sell_avg_price"] = round(
                entry["sell_notional"]
                / sell_quantity,
                6,
            )

        entry["buy_qty"] = round(
            entry["buy_qty"],
            8,
        )
        entry["sell_qty"] = round(
            entry["sell_qty"],
            8,
        )
        entry["buy_notional"] = round(
            entry["buy_notional"],
            2,
        )
        entry["sell_notional"] = round(
            entry["sell_notional"],
            2,
        )

    return {
        "fill_activity_count": len(fills),
        "symbol_count": len(symbol_data),
        "total_buy_notional": round(
            total_buy_notional,
            2,
        ),
        "total_sell_notional": round(
            total_sell_notional,
            2,
        ),
        "net_cash_flow_from_trades": round(
            total_sell_notional
            - total_buy_notional,
            2,
        ),
        "symbols": [
            symbol_data[symbol]
            for symbol in sorted(symbol_data)
        ],
    }


def fetch_and_save_today_orders(
    market_date: date | None = None,
) -> Path:
    """
    获取指定交易日的订单和真实成交，并保存JSON。
    """
    if market_date is None:
        market_date = datetime.now(
            NEW_YORK_TIMEZONE
        ).date()

    _, _, paper = get_alpaca_credentials()

    submitted_orders = (
        fetch_orders_submitted_on_date(
            market_date
        )
    )

    raw_fill_activities = (
        fetch_fill_activities(market_date)
    )

    order_lookup = build_order_lookup(
        submitted_orders
    )

    (
        related_previous_orders,
        warnings,
    ) = fetch_missing_fill_orders(
        fill_activities=raw_fill_activities,
        order_lookup=order_lookup,
    )

    fills = [
        normalize_fill_activity(
            activity=activity,
            order_lookup=order_lookup,
        )
        for activity in raw_fill_activities
    ]

    start_time, end_time = (
        get_market_day_range(market_date)
    )

    if len(submitted_orders) >= MAX_ORDER_RESULTS:
        warnings.append(
            "当日提交订单达到500条，"
            "订单查询结果可能被截断"
        )

    result = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "alpaca",
        "status": "success",
        "data": {
            "market_date": (
                market_date.isoformat()
            ),
            "market_timezone": (
                "America/New_York"
            ),
            "account_mode": (
                "paper" if paper else "live"
            ),
            "query_window": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
            "submitted_orders": {
                "order_count": len(
                    submitted_orders
                ),
                "status_counts": (
                    summarize_order_statuses(
                        submitted_orders
                    )
                ),
                "orders": submitted_orders,
            },
            "fill_related_orders_from_other_dates": {
                "order_count": len(
                    related_previous_orders
                ),
                "orders": related_previous_orders,
            },
            "fills": {
                "fill_count": len(fills),
                "activities": fills,
                "summary": summarize_fills(
                    fills
                ),
            },
            "warnings": warnings,
        },
    }

    output_path = (
        get_project_root()
        / "data"
        / "snapshots"
        / "today_orders.json"
    )

    save_json_atomically(
        output_path,
        result,
    )

    return output_path


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description=(
            "下载指定纽约交易日的订单和成交"
        )
    )

    parser.add_argument(
        "--date",
        dest="market_date",
        help=(
            "纽约交易日期，格式 YYYY-MM-DD。"
            "默认使用当前纽约日期。"
        ),
    )

    arguments = parser.parse_args()

    try:
        market_date = parse_market_date(
            arguments.market_date
        )

        output_path = (
            fetch_and_save_today_orders(
                market_date=market_date
            )
        )

        with output_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            result = json.load(file)

        data = result.get("data", {})

        submitted_orders = data.get(
            "submitted_orders",
            {},
        )

        fills = data.get("fills", {})

        warnings = data.get("warnings", [])

        print("当日订单和成交读取成功")
        print(f"纽约交易日期：{market_date}")
        print(f"保存位置：{output_path}")
        print(
            "当日提交订单数量："
            f"{submitted_orders.get('order_count', 0)}"
        )
        print(
            "真实成交活动数量："
            f"{fills.get('fill_count', 0)}"
        )

        if warnings:
            print("警告：")

            for warning in warnings:
                print(f"- {warning}")

        return 0

    except Exception as error:
        print("当日订单和成交读取失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())