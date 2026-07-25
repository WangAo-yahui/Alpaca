from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from v2.codex.runner import CodexRunResult
from v2.runtime import load_json_object, utc_now_iso


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def copy_v2_config(target_root: Path) -> None:
    target = target_root / "config" / "v2"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PROJECT_ROOT / "config" / "v2",
        target,
    )
    shutil.copytree(
        PROJECT_ROOT / "config" / "universe",
        target_root / "config" / "universe",
    )


def prepare_stage_c_project(
    target_root: Path,
    *,
    stock_count: int = 65,
) -> list[str]:
    copy_v2_config(target_root)
    shutil.copytree(
        PROJECT_ROOT / "prompts" / "v2",
        target_root / "prompts" / "v2",
    )
    shutil.copytree(
        PROJECT_ROOT / "schemas" / "v2",
        target_root / "schemas" / "v2",
    )
    stocks = [
        f"S{index:03d}"
        for index in range(stock_count)
    ]
    etfs = [
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "GLD",
        "TLT",
    ]
    (
        target_root
        / "config"
        / "universe"
        / "sp500.json"
    ).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "as_of_date": "2026-07-23",
                "constituent_security_count": (
                    len(stocks)
                ),
                "symbols": stocks,
            }
        ),
        encoding="utf-8",
    )
    (
        target_root
        / "config"
        / "universe"
        / "etfs.json"
    ).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "as_of_date": "2026-07-23",
                "etfs": [
                    {
                        "symbol": symbol,
                        "enabled": True,
                    }
                    for symbol in etfs
                ],
            }
        ),
        encoding="utf-8",
    )
    symbols = sorted([*stocks, *etfs])
    assets_directory = (
        target_root / "data" / "snapshots"
    )
    assets_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    (
        assets_directory / "assets.json"
    ).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "assets": [
                    {
                        "symbol": symbol,
                        "name": f"{symbol} Name",
                        "status": "active",
                        "tradable": True,
                        "fractionable": True,
                        "shortable": True,
                        "exchange": "NASDAQ",
                    }
                    for symbol in symbols
                ],
            }
        ),
        encoding="utf-8",
    )
    bars_directory = (
        target_root / "data" / "bars" / "daily"
    )
    bars_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    start = datetime(
        2025,
        9,
        27,
        tzinfo=timezone.utc,
    )
    bars = [
        {
            "timestamp": (
                start + timedelta(days=index)
            ).isoformat(),
            "open": 99.5 + index * 0.01,
            "high": 101.0 + index * 0.01,
            "low": 99.0 + index * 0.01,
            "close": 100.0 + index * 0.01,
            "volume": 100000.0 + index,
            "trade_count": 1000,
            "vwap": 100.0 + index * 0.01,
        }
        for index in range(300)
    ]
    for symbol in symbols:
        (
            bars_directory / f"{symbol}.json"
        ).write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "data": {
                        "symbol": symbol,
                        "bars": bars,
                    },
                }
            ),
            encoding="utf-8",
        )
    return symbols


def valid_coarse_output(
    input_payload: dict[str, Any],
    *,
    status: str = "success_local_only",
) -> dict[str, Any]:
    universe = input_payload["universe"]
    items = (
        universe
        if isinstance(universe, list)
        else universe["items"]
    )
    by_symbol = {
        item["symbol"]: item
        for item in items
    }
    required = list(
        input_payload["must_include"]
    )
    selected = [
        by_symbol[symbol]
        for symbol in required
    ]
    selected.extend(
        item
        for item in items
        if item["symbol"] not in set(required)
    )
    selected = selected[:60]
    local_only = status == "success_local_only"
    return {
        "schema_version": "1.0",
        "stage": "coarse_selection",
        "run_date": input_payload["run_date"],
        "generated_at": (
            input_payload["generated_at"]
        ),
        "input_signature": (
            input_payload["input_signature"]
        ),
        "status": status,
        "network_research": {
            "status": (
                "unavailable"
                if local_only
                else "completed"
            ),
            "web_access": not local_only,
            "summary": (
                "Local input only"
                if local_only
                else "Web research completed"
            ),
            "warnings": (
                ["network unavailable; local only"]
                if local_only
                else []
            ),
        },
        "market_summary": (
            "Deterministic coarse research pool"
        ),
        "selection_count": 60,
        "selections": [
            {
                "rank": index,
                "symbol": item["symbol"],
                "asset_type": (
                    item["asset_type"]
                ),
                "sector": item["sector"],
                "industry": item["industry"],
                "research_eligible": (
                    item["research_eligible"]
                ),
                "screen_new_position_eligible": (
                    item[
                        "screen_new_position_eligible"
                    ]
                ),
                "selection_reason": (
                    "Eligible for deeper research"
                ),
                "main_risks": ["Market risk"],
                "key_factors": [
                    "Price and liquidity"
                ],
                "source_references": ["input"],
            }
            for index, item in enumerate(
                selected,
                start=1,
            )
        ],
        "warnings": (
            ["network unavailable; local only"]
            if local_only
            else []
        ),
        "source_references": [
            {
                "id": "input",
                "title": "Stage C input",
                "url": "",
                "source_type": "input",
                "retrieved_at": (
                    input_payload["generated_at"]
                ),
            }
        ],
    }


class FakeCoarseRunner:
    def __init__(
        self,
        *,
        mutate: Any = None,
    ) -> None:
        self.calls = 0
        self.mutate = mutate

    def run(self, workspace: Any) -> CodexRunResult:
        self.calls += 1
        input_payload = load_json_object(
            workspace.input_file
        )
        output = valid_coarse_output(
            input_payload
        )
        if self.mutate is not None:
            self.mutate(output)
        return CodexRunResult(
            payload=output,
            call_record={
                "schema_version": "1.0",
                "stage": "coarse_selection",
                "status": "success",
                "working_directory": str(
                    workspace.root
                ),
                "command": ["fake-codex"],
                "timeout_seconds": 1,
                "retry_count": 0,
                "attempts": [
                    {
                        "attempt": 1,
                        "return_code": 0,
                    }
                ],
                "completed_at": utc_now_iso(),
            },
        )
