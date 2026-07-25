"""构建并持久化 WA Trader v2 的共享、去重标的池。

作用：合并股票、ETF、must-include 与排除清单并生成稳定签名。
重要性：候选空间必须与账户产物隔离且可复现，避免研究输入在轮次间静默漂移。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from v2.config import V2Config
from v2.data._normalization import (
    normalized_symbol,
)
from v2.exceptions import ConfigurationError
from v2.runtime import (
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


SYMBOL_PATTERN = re.compile(
    r"^[A-Z0-9][A-Z0-9.-]{0,14}$"
)


def _valid_symbol(value: object) -> str:
    symbol = normalized_symbol(value)
    return (
        symbol
        if SYMBOL_PATTERN.fullmatch(symbol)
        else ""
    )


def _read_source_symbols(
    path: Path,
    *,
    asset_type: str,
    source_name: str,
) -> tuple[list[str], dict[str, Any]]:
    payload = load_json_object(path)
    symbols: list[str] = []
    if isinstance(payload.get("symbols"), list):
        symbols.extend(
            _valid_symbol(value)
            for value in payload["symbols"]
        )
    if isinstance(payload.get("etfs"), list):
        for item in payload["etfs"]:
            if not isinstance(item, dict):
                continue
            if item.get("enabled", True) is False:
                continue
            symbols.append(
                _valid_symbol(item.get("symbol"))
            )
    symbols = [
        symbol
        for symbol in symbols
        if symbol
    ]
    metadata = {
        "path": source_name,
        "asset_type": asset_type,
        "schema_version": payload.get(
            "schema_version"
        ),
        "as_of_date": payload.get(
            "as_of_date"
        ),
        "declared_count": payload.get(
            "constituent_security_count"
        ),
    }
    return symbols, metadata


def _signature(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def load_universe(
    config: V2Config,
) -> dict[str, Any]:
    universe_config = config.universe
    exclusions = {
        _valid_symbol(value)
        for value in universe_config[
            "excluded_symbols"
        ]
    }
    exclusions.discard("")
    must_include = sorted(
        {
            _valid_symbol(value)
            for value in universe_config[
                "must_include"
            ]
            if _valid_symbol(value)
        }
    )

    by_symbol: dict[
        str,
        dict[str, Any],
    ] = {}
    sources: list[dict[str, Any]] = []

    source_groups = (
        (
            "stock_pool_files",
            "stock",
        ),
        (
            "etf_pool_files",
            "etf",
        ),
    )
    for field, asset_type in source_groups:
        for relative_path in universe_config[field]:
            path = (
                config.project_root
                / str(relative_path)
            ).resolve()
            symbols, metadata = (
                _read_source_symbols(
                    path,
                    asset_type=asset_type,
                    source_name=str(
                        relative_path
                    ),
                )
            )
            sources.append(metadata)
            for symbol in symbols:
                if symbol in exclusions:
                    continue
                entry = by_symbol.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "asset_type": asset_type,
                        "sources": [],
                    },
                )
                if asset_type == "etf":
                    entry["asset_type"] = "etf"
                relative = str(relative_path)
                if relative not in entry["sources"]:
                    entry["sources"].append(
                        relative
                    )

    missing_required = [
        symbol
        for symbol in must_include
        if symbol not in by_symbol
    ]
    if missing_required:
        raise ConfigurationError(
            "必须覆盖标的不在静态股票池中",
            code="UNIVERSE_REQUIRED_SYMBOL_MISSING",
            details={
                "symbols": missing_required
            },
        )

    entries = sorted(
        by_symbol.values(),
        key=lambda item: item["symbol"],
    )
    signature_payload = {
        "config_version": config.config_version,
        "stock_pool_version": (
            universe_config["stock_pool_version"]
        ),
        "etf_pool_version": (
            universe_config["etf_pool_version"]
        ),
        "must_include": must_include,
        "exclusions": sorted(exclusions),
        "entries": entries,
    }
    return {
        "schema_version": "1.0",
        "generated_at": utc_now_iso(),
        "config_version": config.config_version,
        "stock_pool_version": (
            universe_config["stock_pool_version"]
        ),
        "etf_pool_version": (
            universe_config["etf_pool_version"]
        ),
        "symbol_count": len(entries),
        "symbols": [
            entry["symbol"]
            for entry in entries
        ],
        "entries": entries,
        "sources": sources,
        "must_include": must_include,
        "exclusions": sorted(exclusions),
        "input_signature": _signature(
            signature_payload
        ),
    }


def save_universe_snapshot(
    path: Path,
    universe: dict[str, Any],
) -> None:
    atomic_write_json(path, universe)
