from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpaca.trading.enums import AssetClass
from alpaca.trading.requests import GetAssetsRequest

from alpaca_client import create_trading_client
from config import get_project_root, load_symbols
from fetch_account import enum_value, save_json_atomically
from fetch_open_orders import serialize_value


EXPECTED_ASSET_CLASS = "us_equity"
EXPECTED_ACTIVE_STATUS = "active"


def normalize_symbol(value: Any) -> str:
    """将资产代码标准化为大写字符串。"""
    if value is None:
        return ""

    return str(value).strip().upper()


def normalize_boolean(value: Any) -> bool | None:
    """只接受明确的布尔值。"""
    if isinstance(value, bool):
        return value

    return None


def normalize_number(value: Any) -> float | None:
    """将数值安全转换为浮点数。"""
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_exclusion_reasons(
    asset_class: str | None,
    status: str | None,
    tradable: bool | None,
) -> list[str]:
    """生成资产不符合 v1 交易条件的原因。"""
    reasons: list[str] = []

    if asset_class != EXPECTED_ASSET_CLASS:
        reasons.append(
            "asset_class_not_us_equity"
        )

    if status != EXPECTED_ACTIVE_STATUS:
        reasons.append(
            "asset_not_active"
        )

    if tradable is not True:
        reasons.append(
            "asset_not_tradable"
        )

    return reasons


def build_asset_data(
    asset: Any,
) -> dict[str, Any]:
    """
    将 Alpaca Asset 对象转换为可保存的字典。

    v1 的强制交易资格只依赖：
    - asset_class == us_equity
    - status == active
    - tradable == true

    其他字段当前只用于记录和后续扩展。
    """
    symbol = normalize_symbol(
        getattr(asset, "symbol", None)
    )

    asset_class = enum_value(
        getattr(asset, "asset_class", None)
    )

    status = enum_value(
        getattr(asset, "status", None)
    )

    exchange = enum_value(
        getattr(asset, "exchange", None)
    )

    tradable = normalize_boolean(
        getattr(asset, "tradable", None)
    )

    exclusion_reasons = (
        build_exclusion_reasons(
            asset_class=asset_class,
            status=status,
            tradable=tradable,
        )
    )

    attributes = serialize_value(
        getattr(asset, "attributes", None)
    )

    if not isinstance(attributes, list):
        attributes = []

    borrow_status = enum_value(
        getattr(asset, "borrow_status", None)
    )

    return {
        "found": True,
        "id": serialize_value(
            getattr(asset, "id", None)
        ),
        "symbol": symbol,
        "name": getattr(asset, "name", None),
        "asset_class": asset_class,
        "exchange": exchange,
        "status": status,
        "tradable": tradable,
        "fractionable": normalize_boolean(
            getattr(
                asset,
                "fractionable",
                None,
            )
        ),
        "marginable": normalize_boolean(
            getattr(
                asset,
                "marginable",
                None,
            )
        ),
        "shortable": normalize_boolean(
            getattr(
                asset,
                "shortable",
                None,
            )
        ),
        "borrow_status": borrow_status,
        "easy_to_borrow_legacy": (
            normalize_boolean(
                getattr(
                    asset,
                    "easy_to_borrow",
                    None,
                )
            )
        ),
        "min_order_size": normalize_number(
            getattr(
                asset,
                "min_order_size",
                None,
            )
        ),
        "min_trade_increment": (
            normalize_number(
                getattr(
                    asset,
                    "min_trade_increment",
                    None,
                )
            )
        ),
        "price_increment": normalize_number(
            getattr(
                asset,
                "price_increment",
                None,
            )
        ),
        "maintenance_margin_requirement": (
            normalize_number(
                getattr(
                    asset,
                    "maintenance_margin_requirement",
                    None,
                )
            )
        ),
        "attributes": attributes,
        "is_active": (
            status == EXPECTED_ACTIVE_STATUS
        ),
        "is_us_equity": (
            asset_class
            == EXPECTED_ASSET_CLASS
        ),
        "eligible_for_v1": (
            len(exclusion_reasons) == 0
        ),
        "exclusion_reasons": (
            exclusion_reasons
        ),
    }


def build_missing_asset_data(
    symbol: str,
) -> dict[str, Any]:
    """为 Alpaca 主列表中未找到的代码生成记录。"""
    return {
        "found": False,
        "id": None,
        "symbol": symbol,
        "name": None,
        "asset_class": None,
        "exchange": None,
        "status": None,
        "tradable": None,
        "fractionable": None,
        "marginable": None,
        "shortable": None,
        "borrow_status": None,
        "easy_to_borrow_legacy": None,
        "min_order_size": None,
        "min_trade_increment": None,
        "price_increment": None,
        "maintenance_margin_requirement": None,
        "attributes": [],
        "is_active": False,
        "is_us_equity": False,
        "eligible_for_v1": False,
        "exclusion_reasons": [
            "asset_not_found_in_alpaca"
        ],
    }


def fetch_alpaca_us_equities() -> list[Any]:
    """
    一次读取 Alpaca 的美国股票资产主列表。

    不在请求中强制只查询 active，
    这样能够尽可能识别 inactive 资产。
    """
    trading_client = create_trading_client()

    request = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
    )

    assets = trading_client.get_all_assets(
        filter=request
    )

    if isinstance(assets, dict):
        raise RuntimeError(
            "Alpaca资产接口返回了字典，"
            "预期应为Asset对象列表"
        )

    if not isinstance(assets, list):
        raise RuntimeError(
            "Alpaca资产接口返回了未知类型："
            f"{type(assets).__name__}"
        )

    return assets


def build_asset_lookup(
    assets: list[Any],
) -> dict[str, dict[str, Any]]:
    """根据代码建立 Alpaca 资产索引。"""
    lookup: dict[str, dict[str, Any]] = {}

    for asset in assets:
        asset_data = build_asset_data(asset)

        symbol = asset_data["symbol"]

        if not symbol:
            continue

        lookup[symbol] = asset_data

    return lookup


def build_summary(
    asset_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """汇总当前 universe 的资产状态。"""
    found_symbols: list[str] = []
    missing_symbols: list[str] = []
    eligible_symbols: list[str] = []
    ineligible_symbols: list[str] = []
    non_tradable_symbols: list[str] = []
    inactive_symbols: list[str] = []

    exchange_counts: dict[str, int] = {}

    for record in asset_records:
        symbol = record["symbol"]

        if record["found"]:
            found_symbols.append(symbol)
        else:
            missing_symbols.append(symbol)

        if record["eligible_for_v1"]:
            eligible_symbols.append(symbol)
        else:
            ineligible_symbols.append(symbol)

        if record["tradable"] is not True:
            non_tradable_symbols.append(symbol)

        if record["is_active"] is not True:
            inactive_symbols.append(symbol)

        exchange = record.get("exchange")

        if isinstance(exchange, str) and exchange:
            exchange_counts[exchange] = (
                exchange_counts.get(exchange, 0)
                + 1
            )

    return {
        "requested_symbol_count": (
            len(asset_records)
        ),
        "found_symbol_count": (
            len(found_symbols)
        ),
        "missing_symbol_count": (
            len(missing_symbols)
        ),
        "eligible_symbol_count": (
            len(eligible_symbols)
        ),
        "ineligible_symbol_count": (
            len(ineligible_symbols)
        ),
        "found_symbols": found_symbols,
        "missing_symbols": missing_symbols,
        "eligible_symbols": eligible_symbols,
        "ineligible_symbols": (
            ineligible_symbols
        ),
        "non_tradable_symbols": (
            non_tradable_symbols
        ),
        "inactive_symbols": inactive_symbols,
        "exchange_counts": dict(
            sorted(exchange_counts.items())
        ),
    }


def fetch_and_save_assets() -> Path:
    """
    获取 universe 中所有标的的 Alpaca 资产状态。

    保存位置：
    data/snapshots/assets.json
    """
    requested_symbols = load_symbols()

    alpaca_assets = (
        fetch_alpaca_us_equities()
    )

    asset_lookup = build_asset_lookup(
        alpaca_assets
    )

    asset_records: list[dict[str, Any]] = []

    for symbol in requested_symbols:
        normalized_symbol = (
            normalize_symbol(symbol)
        )

        record = asset_lookup.get(
            normalized_symbol
        )

        if record is None:
            record = build_missing_asset_data(
                normalized_symbol
            )

        asset_records.append(record)

    summary = build_summary(asset_records)

    result = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "alpaca_trading_assets",
        "status": "success",
        "data": {
            "eligibility_policy": {
                "required_asset_class": (
                    EXPECTED_ASSET_CLASS
                ),
                "required_status": (
                    EXPECTED_ACTIVE_STATUS
                ),
                "require_tradable": True,
                "fractionable_required": False,
                "marginable_required": False,
                "shortable_required": False,
            },
            "alpaca_master_asset_count": (
                len(alpaca_assets)
            ),
            "summary": summary,
            "assets": asset_records,
        },
    }

    output_path = (
        get_project_root()
        / "data"
        / "snapshots"
        / "assets.json"
    )

    save_json_atomically(
        output_path,
        result,
    )

    return output_path


def main() -> int:
    """命令行入口。"""
    try:
        output_path = fetch_and_save_assets()

        with output_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            import json

            result = json.load(file)

        summary = (
            result
            .get("data", {})
            .get("summary", {})
        )

        print("资产状态读取成功")
        print(f"保存位置：{output_path}")
        print(
            "请求标的数量："
            f"{summary.get('requested_symbol_count', 0)}"
        )
        print(
            "找到标的数量："
            f"{summary.get('found_symbol_count', 0)}"
        )
        print(
            "符合v1交易条件数量："
            f"{summary.get('eligible_symbol_count', 0)}"
        )
        print(
            "不符合v1交易条件数量："
            f"{summary.get('ineligible_symbol_count', 0)}"
        )

        missing_symbols = summary.get(
            "missing_symbols",
            [],
        )

        if missing_symbols:
            print(
                "Alpaca未找到标的："
                + ", ".join(missing_symbols)
            )

        ineligible_symbols = summary.get(
            "ineligible_symbols",
            [],
        )

        if ineligible_symbols:
            print(
                "不符合交易条件标的："
                + ", ".join(
                    ineligible_symbols
                )
            )

        return 0

    except Exception as error:
        print("资产状态读取失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())