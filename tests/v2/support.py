"""构造 WA Trader v2 各阶段的隔离测试项目与确定性 Codex 输出。

作用：集中生成合法 coarse、portfolio、execution 合同和 fake broker 客户端。
重要性：共享基线让安全校验测试只改变一个事实，避免误把 fixture 漂移当成业务结果。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from v2.codex.runner import CodexRunResult
from v2.cli import parse_cli_args
from v2.data.alpaca_client import AlpacaClients
from v2.runtime import load_json_object, utc_now_iso
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_account,
    fake_order,
    fake_position,
)


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
    shutil.copytree(
        PROJECT_ROOT / "strategies",
        target_root / "strategies",
    )
    system_path = target / "system.json"
    system = json.loads(
        system_path.read_text(encoding="utf-8")
    )
    system["operational_profiles"] = [
        "live1",
        "paper1",
    ]
    system_path.write_text(
        json.dumps(
            system,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
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
    policy = input_payload.get("policy", {})
    required_selection_count = int(
        policy.get(
            "required_selection_count",
            60,
        )
        if isinstance(policy, dict)
        else 60
    )
    version_four_contract = bool(
        isinstance(policy, dict)
        and policy.get(
            "codex_supplement_selection"
        )
    )
    raw_shortlists = input_payload.get(
        "python_shortlists",
        {},
    )
    shortlist_symbols = {
        asset_type: {
            str(item.get("symbol", ""))
            for item in (
                raw_shortlists.get(
                    asset_type,
                    [],
                )
                if isinstance(
                    raw_shortlists,
                    dict,
                )
                else []
            )
            if isinstance(item, dict)
        }
        for asset_type in ("stock", "etf")
    }
    required = list(
        input_payload["must_include"]
    )
    selected = [
        by_symbol[symbol]
        for symbol in required
    ]
    ordinary_pool = (
        [
            item
            for item in items
            if item["symbol"]
            in shortlist_symbols.get(
                item["asset_type"],
                set(),
            )
        ]
        if version_four_contract
        else items
    )
    selected.extend(
        item
        for item in ordinary_pool
        if item["symbol"] not in set(required)
    )
    selected = selected[
        :required_selection_count
    ]
    local_only = status == "success_local_only"
    if version_four_contract and not local_only:
        selected_symbols = {
            item["symbol"]
            for item in selected
        }
        supplements = [
            item
            for item in items
            if item["symbol"]
            not in shortlist_symbols.get(
                item["asset_type"],
                set(),
            )
            and item["symbol"]
            not in selected_symbols
        ][:3]
        selected = [
            *selected[
                : required_selection_count
                - len(supplements)
            ],
            *supplements,
        ]
    output = {
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
        "selection_count": (
            required_selection_count
        ),
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
                **(
                    {
                        "selection_origin": (
                            "python_shortlist"
                            if item["symbol"]
                            in shortlist_symbols.get(
                                item[
                                    "asset_type"
                                ],
                                set(),
                            )
                            else "codex_supplement"
                        )
                    }
                    if version_four_contract
                    else {}
                ),
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
    if version_four_contract:
        output["external_discoveries"] = (
            []
            if local_only
            else [
                {
                    "symbol": "DISC1",
                    "asset_type": "stock",
                    "candidate_type": "satellite",
                    "research_only": True,
                    "why_python_may_miss": (
                        "Outside configured universe"
                    ),
                    "thesis": "Test discovery",
                    "primary_evidence": [
                        "Issuer evidence"
                    ],
                    "main_risks": ["Discovery risk"],
                    "next_validation_steps": [
                        "Verify eligibility"
                    ],
                    "source_references": ["web1"],
                },
                {
                    "symbol": "DISCETF",
                    "asset_type": "etf",
                    "candidate_type": (
                        "diversifier_etf"
                    ),
                    "research_only": True,
                    "why_python_may_miss": (
                        "Outside configured universe"
                    ),
                    "thesis": "Test ETF discovery",
                    "primary_evidence": [
                        "Sponsor evidence"
                    ],
                    "main_risks": ["Structure risk"],
                    "next_validation_steps": [
                        "Verify eligibility"
                    ],
                    "source_references": ["web2"],
                },
            ]
        )
        if not local_only:
            output["source_references"].extend(
                [
                    {
                        "id": "web1",
                        "title": "Issuer source",
                        "url": "https://example.com/stock",
                        "source_type": "web",
                        "retrieved_at": input_payload[
                            "generated_at"
                        ],
                    },
                    {
                        "id": "web2",
                        "title": "ETF sponsor source",
                        "url": "https://example.com/etf",
                        "source_type": "web",
                        "retrieved_at": input_payload[
                            "generated_at"
                        ],
                    },
                ]
            )
    return output


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


def valid_portfolio_output(
    input_payload: dict[str, Any],
    *,
    status: str = "success_local_only",
) -> dict[str, Any]:
    """Build a small valid strategic-weight output with no order fields."""

    candidates = [
        item
        for item in input_payload["candidates"]
        if item[
            "screen_new_position_eligible"
        ]
        and not any(
            position["symbol"]
            == item["symbol"]
            for position in input_payload[
                "positions"
            ]
        )
    ][:3]
    generated = datetime.now(
        timezone.utc
    )
    valid_until = generated + timedelta(
        hours=3
    )
    return {
        "schema_version": "1.0",
        "stage": "portfolio_decision",
        "profile_id": input_payload[
            "profile"
        ]["profile_id"],
        "strategy_id": input_payload[
            "release"
        ]["strategy_id"],
        "strategy_version": input_payload[
            "release"
        ]["strategy_version"],
        "run_date": input_payload["run_date"],
        "cycle_id": input_payload["cycle_id"],
        "generated_at": generated.isoformat(),
        "input_signature": input_payload[
            "input_signature"
        ],
        "status": status,
        "network_research": {
            "status": (
                "unavailable"
                if status == "success_local_only"
                else "completed"
            ),
            "web_access": (
                status != "success_local_only"
            ),
            "summary": "Deterministic test research",
            "warnings": (
                ["local only"]
                if status == "success_local_only"
                else []
            ),
        },
        "guidance_response": {
            "summary": "Guidance considered",
            "accepted_points": [],
            "modified_points": [],
            "rejected_points": [
                input_payload[
                    "initial_guidance"
                ].get("raw_text", "")
            ]
            if input_payload[
                "initial_guidance"
            ].get("raw_text")
            else [],
            "constraint_conflicts": [],
        },
        "market_assessment": {
            "regime": "neutral",
            "summary": "Test market",
            "key_risks": ["Market risk"],
        },
        "allocation": {
            "target_cash_weight": "0.76",
            "target_invested_weight": "0.24",
            "target_position_count": 3,
            "maximum_single_symbol_weight": "0.08",
            "maximum_sector_weight": "0.30",
            "deployment_posture": "gradual",
            "rationale": "Diversified test allocation",
        },
        "decisions": [
            {
                "symbol": item["symbol"],
                "current_position": False,
                "in_current_coarse": True,
                "action": "open",
                "conviction": "medium",
                "target_weight": "0.08",
                "maximum_weight": "0.08",
                "priority": index,
                "price_plan": {
                    "currency": "USD",
                    "entry_zone_low": None,
                    "entry_zone_high": None,
                    "do_not_chase_above": None,
                    "review_below": None,
                    "notes": "Recheck live price",
                },
                "protection_plan": {
                    "style": "thesis_break",
                    "reference_price": None,
                    "maximum_loss_fraction": "0.10",
                    "notes": "Review thesis",
                },
                "thesis": "Eligible candidate",
                "risks": ["Market risk"],
                "catalysts": [],
                "portfolio_role": "Diversifier",
                "execution_checks": [
                    "Refresh price"
                ],
                "source_references": ["input"],
            }
            for index, item in enumerate(
                candidates,
                start=1,
            )
        ],
        "open_order_assessments": [
            {
                "order_reference": (
                    f"{order['symbol']}:"
                    f"{order['side']}"
                ),
                "symbol": order["symbol"],
                "assessment": "review",
                "reason": "Refresh before execution",
                "conflicts_with_target": False,
            }
            for order in input_payload[
                "open_orders"
            ]
        ],
        "watchlist": [],
        "execution_focus": [
            "Refresh quotes before any order"
        ],
        "requires_rebalance_next_cycle": False,
        "valid_until": valid_until.isoformat(),
        "warnings": (
            ["local only"]
            if status == "success_local_only"
            else []
        ),
        "source_references": [
            {
                "id": "input",
                "title": "Portfolio input",
                "url": "",
                "source_type": "input",
                "retrieved_at": (
                    input_payload["generated_at"]
                ),
            }
        ],
    }


class FakePortfolioRunner:
    def __init__(
        self,
        *,
        mutate: Any = None,
    ) -> None:
        self.calls = 0
        self.mutate = mutate

    def run(
        self,
        workspace: Any,
    ) -> CodexRunResult:
        self.calls += 1
        input_payload = load_json_object(
            workspace.input_file
        )
        output = valid_portfolio_output(
            input_payload
        )
        if self.mutate is not None:
            self.mutate(output)
        return CodexRunResult(
            payload=output,
            call_record={
                "schema_version": "1.0",
                "stage": "portfolio_decision",
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


def valid_execution_output(
    input_payload: dict[str, Any],
    *,
    status: str = "success_local_only",
) -> dict[str, Any]:
    """Build a valid Stage E intent without quantities or broker requests."""

    generated = datetime.now(
        timezone.utc
    )
    snapshot = input_payload[
        "execution_snapshot"
    ]
    market_phase = snapshot["market_phase"]
    permission = input_payload[
        "trade_permission"
    ]["submission_enabled"]
    executable_phase = market_phase in {
        "regular_session",
        "before_market_open",
        "after_market_close",
    }
    decisions: list[dict[str, Any]] = []
    for item in input_payload[
        "portfolio"
    ]["decisions"]:
        symbol = item["symbol"]
        action = item["action"]
        directional = action in {
            "open",
            "increase",
            "reduce",
            "close",
        }
        executable = (
            permission
            and executable_phase
            and directional
        )
        side = (
            "buy"
            if action in {"open", "increase"}
            else "sell"
            if action in {"reduce", "close"}
            else "none"
        )
        quote = snapshot["quotes"].get(
            symbol,
            {},
        )
        decisions.append(
            {
                "symbol": symbol,
                "portfolio_action": action,
                "execution_decision": (
                    "approve"
                    if executable
                    else "defer"
                ),
                "side": side if executable else "none",
                "target_weight": item[
                    "target_weight"
                ],
                "maximum_weight": item[
                    "maximum_weight"
                ],
                "execution_fraction": (
                    "0.50" if executable else "0"
                ),
                "urgency": (
                    "normal" if executable else "none"
                ),
                "price_condition": {
                    "reference": (
                        "ask" if executable else "none"
                    ),
                    "limit_price": (
                        str(quote.get("ask_price"))
                        if executable
                        else None
                    ),
                    "do_not_execute_above": None,
                    "review_below": None,
                },
                "order_intent": {
                    "preferred_type": (
                        "limit" if executable else "none"
                    ),
                    "time_in_force_preference": (
                        "day" if executable else "none"
                    ),
                    "extended_hours_requested": (
                        executable
                        and market_phase
                        != "regular_session"
                    ),
                    "allow_queue": executable,
                    "allow_partial_fill": executable,
                },
                "decision_reason": (
                    "Fresh facts permit an intent"
                    if executable
                    else "No executable market phase"
                ),
                "execution_risks": [],
                "required_checks": [
                    "Order builder must revalidate"
                ],
                "source_references": ["input"],
            }
        )
    local_only = status == "success_local_only"
    assets = snapshot.get("assets", {})
    positions = {
        str(item.get("symbol", "")).upper(): item
        for item in snapshot.get("positions", [])
        if isinstance(item, dict)
        and item.get("symbol")
        and (
            not isinstance(
                assets.get(
                    str(
                        item.get(
                            "symbol",
                            "",
                        )
                    ).upper()
                ),
                dict,
            )
            or assets[
                str(
                    item.get(
                        "symbol",
                        "",
                    )
                ).upper()
            ].get("asset_class")
            != "crypto"
        )
    }
    entry_symbols = {
        str(item["symbol"]).upper()
        for item in decisions
        if item["execution_decision"] == "approve"
        and item["side"] == "buy"
        and item["portfolio_action"]
        in {"open", "increase"}
    }
    protection_plans = []
    for symbol in sorted(
        {*positions, *entry_symbols}
    ):
        position = positions.get(symbol, {})
        reference = float(
            position.get("current_price")
            or snapshot.get(
                "quotes",
                {},
            ).get(
                symbol,
                {},
            ).get("ask_price")
            or 100
        )
        protection_plans.append(
            valid_protection_plan(
                symbol,
                reference=reference,
                apply_to=(
                    "both"
                    if (
                        symbol in positions
                        and symbol in entry_symbols
                    )
                    else (
                        "existing_position"
                        if symbol in positions
                        else "new_entry"
                    )
                ),
            )
        )
    return {
        "schema_version": "1.0",
        "stage": "execution_decision",
        "profile_id": input_payload[
            "profile"
        ]["profile_id"],
        "strategy_id": input_payload[
            "release"
        ]["strategy_id"],
        "strategy_version": input_payload[
            "release"
        ]["strategy_version"],
        "run_date": input_payload["run_date"],
        "cycle_id": input_payload["cycle_id"],
        "generated_at": generated.isoformat(),
        "input_signature": input_payload[
            "input_signature"
        ],
        "status": status,
        "network_research": {
            "status": (
                "unavailable"
                if local_only
                else "completed"
            ),
            "web_access": not local_only,
            "summary": "Deterministic execution test",
            "warnings": (
                ["local only"] if local_only else []
            ),
        },
        "market_assessment": {
            "market_phase": market_phase,
            "summary": "Latest execution facts reviewed",
            "key_risks": [],
        },
        "review_response": {
            "summary": "Review honored",
            "honored_prohibitions": [],
            "honored_constraints": [],
            "rejected_requests": [],
            "unresolved_hard_constraints": [],
        },
        "portfolio_response": {
            "summary": "Portfolio checked",
            "modified_symbols": [],
            "deferred_symbols": [
                item["symbol"]
                for item in decisions
                if item["execution_decision"]
                == "defer"
            ],
            "rejected_symbols": [],
        },
        "decisions": decisions,
        "protection_plans": protection_plans,
        "open_order_actions": [
            {
                "order_reference": str(
                    order.get(
                        "client_order_id",
                        order.get("id", ""),
                    )
                ),
                "symbol": order["symbol"],
                "action": "review",
                "reason": "No broker action in Stage E",
            }
            for order in snapshot[
                "open_orders"
            ]
        ],
        "requires_portfolio_replan": False,
        "requires_manual_review": False,
        "valid_until": (
            generated
            + timedelta(minutes=30)
        ).isoformat(),
        "warnings": (
            ["network unavailable; local only"]
            if local_only
            else []
        ),
        "source_references": [
            {
                "id": "input",
                "title": "Stage E input",
                "url": "",
                "source_type": "input",
                "retrieved_at": (
                    input_payload["generated_at"]
                ),
            }
        ],
    }


def valid_protection_plan(
    symbol: str,
    *,
    reference: float = 100.0,
    apply_to: str = "existing_position",
    mode: str = "oco",
) -> dict[str, Any]:
    """Return one schema-valid deterministic long protection plan."""

    return {
        "symbol": symbol,
        "mode": mode,
        "apply_to": apply_to,
        "coverage_fraction": "1",
        "time_in_force": "day",
        "take_profit_price": (
            f"{reference * 1.10:.2f}"
            if mode
            in {
                "take_profit",
                "oco",
                "bracket",
                "oto_take_profit",
            }
            else None
        ),
        "stop_price": (
            f"{reference * 0.90:.2f}"
            if mode
            in {
                "stop",
                "stop_limit",
                "oco",
                "bracket",
                "oto_stop",
            }
            else None
        ),
        "stop_limit_price": (
            f"{reference * 0.89:.2f}"
            if mode
            in {
                "stop_limit",
                "oco",
                "bracket",
            }
            else None
        ),
        "trail_price": (
            "5.00"
            if mode == "trailing_stop"
            else None
        ),
        "trail_percent": None,
        "stages": [],
        "reason": (
            "Deterministic full position protection"
        ),
    }


class FakeExecutionRunner:
    def __init__(
        self,
        *,
        mutate: Any = None,
    ) -> None:
        self.calls = 0
        self.mutate = mutate

    def run(
        self,
        workspace: Any,
    ) -> CodexRunResult:
        self.calls += 1
        input_payload = load_json_object(
            workspace.input_file
        )
        output = valid_execution_output(
            input_payload
        )
        if self.mutate is not None:
            self.mutate(output)
        return CodexRunResult(
            payload=output,
            call_record={
                "schema_version": "1.0",
                "stage": "execution_decision",
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


def stage_d_options(*extra: str):
    return parse_cli_args(
        [
            "--profile",
            "paper1",
            "--run-date",
            "2026-07-23",
            "--unattended",
            "--allow-trade",
            "--bind-account",
            *extra,
        ]
    )


def stage_d_clients(
    *,
    cash: str = "10000.50",
    positions: list[object] | None = None,
    orders: list[object] | None = None,
) -> AlpacaClients:
    return AlpacaClients(
        trading=FakeTradingClient(
            account=fake_account(cash=cash),
            positions=(
                positions
                if positions is not None
                else [fake_position("S064")]
            ),
            open_orders=(
                orders
                if orders is not None
                else [fake_order("S063")]
            ),
            today_orders=[],
        ),
        stock_data=FakeStockDataClient(
            quotes={
                symbol: SimpleNamespace(
                    bid_price="99.90",
                    ask_price="100.00",
                    bid_size="10",
                    ask_size="12",
                    timestamp=datetime.now(
                        timezone.utc
                    ),
                )
                for symbol in [
                    *(
                        f"S{index:03d}"
                        for index in range(65)
                    ),
                    "SPY",
                    "QQQ",
                    "IWM",
                    "DIA",
                    "GLD",
                    "TLT",
                ]
            },
            trades={
                symbol: SimpleNamespace(
                    price="99.95",
                    size="5",
                    timestamp=datetime.now(
                        timezone.utc
                    ),
                )
                for symbol in [
                    *(
                        f"S{index:03d}"
                        for index in range(65)
                    ),
                    "SPY",
                    "QQQ",
                    "IWM",
                    "DIA",
                    "GLD",
                    "TLT",
                ]
            },
        ),
    )


def stage_e_fixture(
    target_root: Path,
    *,
    execution_runner: Any = None,
    clients: AlpacaClients | None = None,
):
    """Create an isolated, fully validated Stage E test cycle."""

    from v2.main import run_stage_e

    prepare_stage_c_project(target_root)
    return run_stage_e(
        stage_d_options(),
        project_root=target_root,
        clients=clients or stage_d_clients(),
        coarse_runner=FakeCoarseRunner(),
        portfolio_runner=FakePortfolioRunner(),
        execution_runner=(
            execution_runner
            or FakeExecutionRunner()
        ),
    )
