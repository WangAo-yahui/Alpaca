from datetime import datetime,timezone
from pathlib import Path
from typing import Any

from alpaca_client import create_trading_client
from config import get_project_root

from fetch_account import enum_value, save_json_atomically

def build_position_data(position: Any) -> dict[str, Any]:
    """
    从 Alpaca 持仓对象中提取需要保存的字段。
    """
    return {
        "symbol": position.symbol,
        "asset_class": enum_value(position.asset_class),
        "exchange": enum_value(position.exchange),
        "side": enum_value(position.side),
        "qty": position.qty,
        "qty_available": position.qty_available,
        "avg_entry_price": position.avg_entry_price,
        "current_price": position.current_price,
        "market_value": position.market_value,
        "cost_basis": position.cost_basis,
        "unrealized_pl": position.unrealized_pl,
        "unrealized_plpc": position.unrealized_plpc,
        "unrealized_intraday_pl": position.unrealized_intraday_pl,
        "unrealized_intraday_plpc": position.unrealized_intraday_plpc,
        "lastday_price": position.lastday_price,
        "change_today": position.change_today,
    }

def fetch_and_save_positions() -> Path:
    """
    获取当前全部持仓并保存为 JSON。

    即使当前没有持仓，也会正常保存一个空数组。
    """
    trading_client = create_trading_client()
    positions = trading_client.get_all_positions()

    position_data = [
        build_position_data(position)
        for position in positions
    ]

    position_data.sort(
        key=lambda item: item["symbol"]
    )

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "alpaca",
        "status": "success",
        "data": {
            "position_count": len(position_data),
            "positions": position_data,
        },
    }

    output_path = (
        get_project_root()
        / "data"
        / "snapshots"
        / "positions.json"
    )

    save_json_atomically(output_path, result)

    return output_path



def main() -> int:
    """单独运行本文件时，测试持仓接口。"""
    try:
        output_path = fetch_and_save_positions()

        print("Alpaca 持仓读取成功")
        print(f"保存位置：{output_path}")

        return 0

    except Exception as error:
        print("Alpaca 持仓读取失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())





