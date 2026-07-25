import json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

from alpaca_client import create_trading_client
from config import get_project_root

def enum_value(value:Any) -> Any:
    """
    如果传入的是枚举对象，返回枚举的实际值。

    普通字符串、数字、布尔值和 None 会原样返回。
    """
    return getattr(value, "value", value)

def build_account_data(account:Any) -> dict[str,Any]:
    """
    从 Alpaca 返回的账户对象中，提取当前项目需要的字段。

    不保存 API 密钥、账户 ID 和完整账户号码。
    """
    return{
        "status": enum_value(account.status),
        "currency": account.currency,
        "buying_power": account.buying_power,
        "non_marginable_buying_power": account.non_marginable_buying_power,
        "cash": account.cash,
        "equity": account.equity,
        "last_equity": account.last_equity,
        "long_market_value": account.long_market_value,
        "short_market_value": account.short_market_value,
        "initial_margin": account.initial_margin,
        "maintenance_margin": account.maintenance_margin,
        "trading_blocked": account.trading_blocked,
        "transfers_blocked": account.transfers_blocked,
        "account_blocked": account.account_blocked,
        "trade_suspended_by_user": account.trade_suspended_by_user,
        "multiplier": account.multiplier,
        "shorting_enabled": account.shorting_enabled,
    }

def save_json_atomically(file_path: Path, content: dict[str, Any]) -> None:
    """
    安全写入 JSON。

    先写入临时文件，成功后再替换正式文件。
    这样程序中途失败时，不会破坏原来的 JSON。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = file_path.with_suffix(
        file_path.suffix + ".tmp"
    )

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(
            content,
            file,
            ensure_ascii=False,
            indent=2,
        )

    temporary_path.replace(file_path)

def fetch_and_save_account() -> Path:
    """
    获取 Alpaca 账户信息并保存。

    返回保存后的 JSON 文件路径。
    """
    trading_client = create_trading_client()
    account = trading_client.get_account()

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "alpaca",
        "status": "success",
        "data": build_account_data(account),
    }

    output_path = (
        get_project_root()
        / "data"
        / "snapshots"
        / "account.json"
    )

    save_json_atomically(output_path, result)

    return output_path

def main() -> int:
    """单独运行本文件时，测试账户接口是否正常。"""
    try:
        output_path = fetch_and_save_account()

        print("Alpaca 账户信息读取成功")
        print(f"保存位置：{output_path}")

        return 0

    except Exception as error:
        print("Alpaca 账户信息读取失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())




