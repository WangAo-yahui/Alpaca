import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def get_project_root() -> Path:
    """返回项目根目录。"""
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = get_project_root()

ENV_PATH = PROJECT_ROOT / ".env"

LEGACY_UNIVERSE_PATH = (
    PROJECT_ROOT
    / "config"
    / "universe.json"
)

UNIVERSE_DIRECTORY = (
    PROJECT_ROOT
    / "config"
    / "universe"
)

SP500_UNIVERSE_PATH = (
    UNIVERSE_DIRECTORY
    / "sp500.json"
)

ETF_UNIVERSE_PATH = (
    UNIVERSE_DIRECTORY
    / "etfs.json"
)

CORE_SYMBOLS_PATH = (
    UNIVERSE_DIRECTORY
    / "core_symbols.json"
)


def load_environment() -> None:
    """
    加载项目根目录中的 .env。

    已经存在于系统环境变量中的值不会被覆盖。
    """
    load_dotenv(
        dotenv_path=ENV_PATH,
        override=False,
    )


def parse_boolean_environment(
    variable_name: str,
    default: bool,
) -> bool:
    """将环境变量解析为布尔值。"""
    raw_value = os.getenv(variable_name)

    if raw_value is None:
        return default

    normalized_value = (
        raw_value
        .strip()
        .lower()
    )

    if normalized_value in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }:
        return True

    if normalized_value in {
        "0",
        "false",
        "no",
        "n",
        "off",
    }:
        return False

    raise ValueError(
        f"环境变量 {variable_name} "
        "必须是布尔值，例如 true 或 false"
    )


def get_alpaca_credentials(
) -> tuple[str, str, bool]:
    """
    读取 Alpaca 凭据和账户模式。

    返回：
    - API Key
    - Secret Key
    - 是否使用模拟盘
    """
    load_environment()

    api_key = os.getenv(
        "ALPACA_API_KEY",
        "",
    ).strip()

    secret_key = os.getenv(
        "ALPACA_SECRET_KEY",
        "",
    ).strip()

    paper = parse_boolean_environment(
        variable_name="ALPACA_PAPER",
        default=True,
    )

    missing_variables: list[str] = []

    if not api_key:
        missing_variables.append(
            "ALPACA_API_KEY"
        )

    if not secret_key:
        missing_variables.append(
            "ALPACA_SECRET_KEY"
        )

    if missing_variables:
        raise ValueError(
            "缺少 Alpaca 环境变量："
            + ", ".join(missing_variables)
        )

    return api_key, secret_key, paper


def normalize_symbol(value: Any) -> str:
    """将股票代码标准化为大写字符串。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def deduplicate_symbols(
    symbols: list[str],
) -> list[str]:
    """按原始顺序对股票代码去重。"""
    result: list[str] = []
    seen: set[str] = set()

    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)

        if not symbol:
            continue

        if symbol in seen:
            continue

        seen.add(symbol)
        result.append(symbol)

    return result


def load_json_object(
    file_path: Path,
) -> dict[str, Any]:
    """读取JSON文件并确认顶层是对象。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"没有找到配置文件：{file_path}"
        )

    try:
        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"JSON格式错误：{file_path}；"
            f"{error}"
        ) from error

    if not isinstance(payload, dict):
        raise ValueError(
            "配置文件顶层必须是JSON对象："
            f"{file_path}"
        )

    return payload


def extract_symbols_field(
    payload: dict[str, Any],
    file_path: Path,
) -> list[str]:
    """
    从带有 symbols 字段的配置中读取代码。

    支持格式：

    {
      "symbols": ["AAPL", "MSFT"]
    }
    """
    raw_symbols = payload.get("symbols")

    if not isinstance(raw_symbols, list):
        raise ValueError(
            f"{file_path} 中的 symbols "
            "必须是数组"
        )

    invalid_values = [
        value
        for value in raw_symbols
        if not isinstance(value, str)
    ]

    if invalid_values:
        raise ValueError(
            f"{file_path} 的 symbols "
            "只能包含字符串"
        )

    return deduplicate_symbols(
        raw_symbols
    )


def load_symbol_file(
    file_path: Path,
    required: bool = False,
) -> list[str]:
    """读取普通 symbols 配置文件。"""
    if not file_path.exists():
        if required:
            raise FileNotFoundError(
                f"没有找到配置文件：{file_path}"
            )

        return []

    payload = load_json_object(file_path)

    return extract_symbols_field(
        payload=payload,
        file_path=file_path,
    )


def load_etf_file(
    file_path: Path,
) -> list[str]:
    """
    读取ETF候选配置。

    支持简单格式：

    {
      "symbols": ["SPY", "QQQ"]
    }

    也支持扩展格式：

    {
      "etfs": [
        {
          "symbol": "SPY",
          "category": "broad_market",
          "enabled": true
        }
      ]
    }
    """
    if not file_path.exists():
        return []

    payload = load_json_object(file_path)

    if "symbols" in payload:
        return extract_symbols_field(
            payload=payload,
            file_path=file_path,
        )

    raw_etfs = payload.get("etfs")

    if not isinstance(raw_etfs, list):
        raise ValueError(
            f"{file_path} 必须包含 "
            "symbols 数组或 etfs 数组"
        )

    symbols: list[str] = []

    for index, entry in enumerate(raw_etfs):
        if isinstance(entry, str):
            symbol = normalize_symbol(entry)

            if symbol:
                symbols.append(symbol)

            continue

        if not isinstance(entry, dict):
            raise ValueError(
                f"{file_path} 中 etfs[{index}] "
                "必须是字符串或对象"
            )

        enabled = entry.get(
            "enabled",
            True,
        )

        if not isinstance(enabled, bool):
            raise ValueError(
                f"{file_path} 中 etfs[{index}]."
                "enabled 必须是布尔值"
            )

        if not enabled:
            continue

        symbol = normalize_symbol(
            entry.get("symbol")
        )

        if not symbol:
            raise ValueError(
                f"{file_path} 中 etfs[{index}] "
                "缺少有效的 symbol"
            )

        symbols.append(symbol)

    return deduplicate_symbols(symbols)


def split_universe_enabled() -> bool:
    """
    判断是否已经完整启用拆分后的标的池。

    只有三个新配置文件全部存在时，
    才切换到新结构。

    这样可以逐个创建配置文件，
    在配置尚未完成期间继续使用旧版
    config/universe.json。
    """
    required_paths = (
        SP500_UNIVERSE_PATH,
        ETF_UNIVERSE_PATH,
        CORE_SYMBOLS_PATH,
    )

    return all(
        path.exists()
        for path in required_paths
    )


def load_symbol_groups(
) -> dict[str, list[str]]:
    """
    读取分组后的完整标的池。

    返回：
    - core：始终优先检查的核心标的
    - stocks：股票候选池
    - etfs：ETF候选池
    - all_symbols：合并并去重后的完整列表

    在新目录尚未建立时，自动回退到旧版
    config/universe.json。
    """
    if not split_universe_enabled():
        legacy_symbols = load_symbol_file(
            file_path=LEGACY_UNIVERSE_PATH,
            required=True,
        )

        if not legacy_symbols:
            raise ValueError(
                "旧版 universe.json "
                "没有有效标的"
            )

        return {
            "core": [],
            "stocks": legacy_symbols,
            "etfs": [],
            "all_symbols": legacy_symbols,
        }

    core_symbols = load_symbol_file(
        file_path=CORE_SYMBOLS_PATH,
        required=False,
    )

    stock_symbols = load_symbol_file(
        file_path=SP500_UNIVERSE_PATH,
        required=False,
    )

    etf_symbols = load_etf_file(
        file_path=ETF_UNIVERSE_PATH,
    )

    all_symbols = deduplicate_symbols(
        core_symbols
        + stock_symbols
        + etf_symbols
    )

    if not all_symbols:
        raise ValueError(
            "拆分后的标的池为空。请检查："
            "config/universe/core_symbols.json、"
            "sp500.json 和 etfs.json"
        )

    stock_symbol_set = set(stock_symbols)
    etf_symbol_set = set(etf_symbols)

    overlap = (
        stock_symbol_set
        & etf_symbol_set
    )

    if overlap:
        raise ValueError(
            "以下代码同时出现在股票池和ETF池："
            + ", ".join(sorted(overlap))
        )

    return {
        "core": core_symbols,
        "stocks": stock_symbols,
        "etfs": etf_symbols,
        "all_symbols": all_symbols,
    }


def load_symbols() -> list[str]:
    """
    返回所有需要采集数据的标的代码。

    当前旧版 universe.json 仍可继续使用。
    新结构建立后，会自动切换为：
    核心标的 + 标普股票池 + ETF池。
    """
    groups = load_symbol_groups()

    return groups["all_symbols"]


def get_symbol_type(
    symbol: str,
) -> str:
    """
    返回标的类型。

    可能值：
    - stock
    - etf
    - core
    - unknown

    核心标的如果同时存在于股票池或ETF池，
    优先返回实际资产类别。
    """
    normalized_symbol = normalize_symbol(
        symbol
    )

    groups = load_symbol_groups()

    if normalized_symbol in set(
        groups["etfs"]
    ):
        return "etf"

    if normalized_symbol in set(
        groups["stocks"]
    ):
        return "stock"

    if normalized_symbol in set(
        groups["core"]
    ):
        return "core"

    return "unknown"


def get_universe_metadata(
) -> dict[str, Any]:
    """返回当前标的池的基本统计信息。"""
    groups = load_symbol_groups()

    return {
        "mode": (
            "split"
            if split_universe_enabled()
            else "legacy"
        ),
        "core_symbol_count": len(
            groups["core"]
        ),
        "stock_symbol_count": len(
            groups["stocks"]
        ),
        "etf_symbol_count": len(
            groups["etfs"]
        ),
        "total_symbol_count": len(
            groups["all_symbols"]
        ),
        "source_files": {
            "legacy": str(
                LEGACY_UNIVERSE_PATH
            ),
            "sp500": str(
                SP500_UNIVERSE_PATH
            ),
            "etfs": str(
                ETF_UNIVERSE_PATH
            ),
            "core_symbols": str(
                CORE_SYMBOLS_PATH
            ),
        },
    }