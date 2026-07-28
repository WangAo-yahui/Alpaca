"""编排 Stage E 执行意图的输入、Codex 调用、校验与原子保存。

作用：读取最新 execution snapshot、组合方案和两轮用户意见，形成第三阶段执行判断。
重要性：校验失败不得安装 output，本阶段不得构建、取消、替换或提交任何实际订单。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
)
from pathlib import Path
from typing import Any, Mapping, Protocol

from v2.codex.runner import (
    CodexRunResult,
    ExecutionCodexRunner,
    codex_runner_settings,
)
from v2.codex.validation import (
    preflight_output_schema,
)
from v2.codex.workspace import (
    ExecutionWorkspace,
    prepare_execution_workspace,
)
from v2.config import V2Config
from v2.crypto_liquidation import (
    automatic_crypto_liquidation_enabled,
    is_crypto_asset,
)
from v2.exceptions import (
    CodexOutputValidationError,
    SafetyBlockedError,
)
from v2.guidance import (
    load_initial_guidance,
)
from v2.models.execution import (
    ExecutionInputBuildResult,
    ExecutionValidationResult,
    build_execution_input,
    validate_execution_output,
)
from v2.models.state import CycleState
from v2.profiles import load_risk_profile
from v2.releases import (
    StrategyRelease,
    load_strategy_release,
)
from v2.review import load_user_review
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


class ExecutionRunner(Protocol):
    def run(
        self,
        workspace: ExecutionWorkspace,
    ) -> CodexRunResult: ...


NON_EXECUTABLE_DECISIONS = frozenset(
    {"defer", "reject", "no_action"}
)


def _mapping(
    value: object,
) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_decimal(
    value: object,
) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return result.is_finite() and result > 0


def _force_live_crypto_liquidations(
    payload: dict[str, Any],
    *,
    input_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Override model discretion for available Live Crypto positions."""

    profile = _mapping(input_payload.get("profile"))
    permission = _mapping(
        input_payload.get("trade_permission")
    )
    execution_policy = _mapping(
        input_payload.get("execution_policy")
    )
    policy = _mapping(
        execution_policy.get("crypto_liquidation")
    )
    if not (
        profile.get("environment") == "live"
        and permission.get("submission_enabled")
        is True
        and automatic_crypto_liquidation_enabled(
            policy
        )
    ):
        return dict(payload), ()

    snapshot = _mapping(
        input_payload.get("execution_snapshot")
    )
    assets = _mapping(snapshot.get("assets"))
    positions = snapshot.get("positions")
    positions = (
        positions
        if isinstance(positions, list)
        else []
    )
    raw_decisions = payload.get("decisions")
    decisions: list[object] = [
        (
            dict(item)
            if isinstance(item, Mapping)
            else item
        )
        for item in (
            raw_decisions
            if isinstance(raw_decisions, list)
            else []
        )
    ]
    decision_indexes = {
        str(item.get("symbol", "")).upper(): index
        for index, item in enumerate(decisions)
        if isinstance(item, Mapping)
        and item.get("symbol")
    }
    forced_symbols: list[str] = []
    for raw_position in positions:
        position = _mapping(raw_position)
        symbol = str(
            position.get("symbol", "")
        ).upper()
        asset = _mapping(assets.get(symbol))
        if not (
            symbol
            and is_crypto_asset(asset)
            and asset.get("status") == "active"
            and asset.get("tradable") is True
            and _positive_decimal(
                position.get("available_quantity")
            )
        ):
            continue
        existing_index = decision_indexes.get(
            symbol
        )
        existing = (
            decisions[existing_index]
            if existing_index is not None
            else {}
        )
        existing = dict(
            existing
            if isinstance(existing, Mapping)
            else {}
        )
        forced = {
            **existing,
            "symbol": symbol,
            "portfolio_action": "close",
            "execution_decision": "approve",
            "side": "sell",
            "target_weight": "0",
            "maximum_weight": "0",
            "execution_fraction": "1",
            "urgency": "high",
            "price_condition": {
                "reference": "last_trade",
                "limit_price": None,
                "do_not_execute_above": None,
                "review_below": None,
            },
            "order_intent": {
                "preferred_type": "market",
                "time_in_force_preference": "gtc",
                "extended_hours_requested": False,
                "allow_queue": False,
                "allow_partial_fill": True,
            },
            "decision_reason": (
                "Python policy requires immediate liquidation of every "
                "available Live Crypto position into USD; model defer/hold "
                "is not permitted."
            ),
            "execution_risks": [
                "A market order may fill at a price different from the last "
                "broker valuation.",
                "USD proceeds are unavailable to the equity strategy until "
                "broker reconciliation confirms the sale.",
            ],
            "required_checks": [
                "Revalidate available quantity immediately before submission.",
                "Use the asset min_trade_increment and min_order_size.",
                "Do not submit a duplicate sell while an active sell exists.",
            ],
            "source_references": [
                "execution_snapshot",
                "automatic_crypto_liquidation_policy",
            ],
        }
        if existing_index is None:
            decision_indexes[symbol] = len(
                decisions
            )
            decisions.append(forced)
        else:
            decisions[existing_index] = forced
        forced_symbols.append(symbol)

    if not forced_symbols:
        return dict(payload), ()
    deferred_for_liquidation: list[str] = []
    forced_set = set(forced_symbols)
    for index, raw_decision in enumerate(
        decisions
    ):
        if not isinstance(
            raw_decision,
            Mapping,
        ):
            continue
        symbol = str(
            raw_decision.get("symbol", "")
        ).upper()
        if (
            symbol in forced_set
            or raw_decision.get(
                "execution_decision"
            )
            not in {"approve", "modify"}
        ):
            continue
        decision = dict(raw_decision)
        decision.update(
            {
                "execution_decision": "defer",
                "side": "none",
                "execution_fraction": "0",
                "urgency": "none",
                "price_condition": {
                    "reference": "none",
                    "limit_price": None,
                    "do_not_execute_above": None,
                    "review_below": None,
                },
                "order_intent": {
                    "preferred_type": "none",
                    "time_in_force_preference": "none",
                    "extended_hours_requested": False,
                    "allow_queue": False,
                    "allow_partial_fill": False,
                },
                "decision_reason": (
                    "Deferred by Python until every automatic Crypto "
                    "liquidation is broker-confirmed and USD buying power "
                    "is refreshed."
                ),
                "required_checks": [
                    "Confirm Crypto liquidation status by broker order ID.",
                    "Refresh cash and buying power before equity deployment.",
                ],
            }
        )
        decisions[index] = decision
        if symbol:
            deferred_for_liquidation.append(
                symbol
            )
    result = dict(payload)
    result["decisions"] = decisions
    response = dict(
        _mapping(result.get("portfolio_response"))
    )
    modified = response.get("modified_symbols")
    response["modified_symbols"] = list(
        dict.fromkeys(
            [
                *(
                    modified
                    if isinstance(modified, list)
                    else []
                ),
                *forced_symbols,
            ]
        )
    )
    deferred = response.get("deferred_symbols")
    response["deferred_symbols"] = list(
        dict.fromkeys(
            [
                *[
                    symbol
                    for symbol in (
                        deferred
                        if isinstance(
                            deferred,
                            list,
                        )
                        else []
                    )
                    if str(symbol).upper()
                    not in forced_set
                ],
                *deferred_for_liquidation,
            ]
        )
    )
    result["portfolio_response"] = response
    warnings = result.get("warnings")
    result["warnings"] = [
        *(
            warnings
            if isinstance(warnings, list)
            else []
        ),
        (
            "Python强制生成Live Crypto全量市价清仓意图："
            + ",".join(forced_symbols)
            + (
                "；在确认USD前延后其他执行："
                + ",".join(
                    deferred_for_liquidation
                )
                if deferred_for_liquidation
                else ""
            )
        ),
    ]
    return result, tuple(forced_symbols)


def _automatic_crypto_liquidation_output(
    input_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]] | None:
    """Build Stage E locally so a Crypto sell does not wait for Codex."""

    profile = _mapping(input_payload.get("profile"))
    permission = _mapping(
        input_payload.get("trade_permission")
    )
    execution_policy = _mapping(
        input_payload.get("execution_policy")
    )
    policy = _mapping(
        execution_policy.get("crypto_liquidation")
    )
    if not (
        profile.get("environment") == "live"
        and permission.get("submission_enabled")
        is True
        and automatic_crypto_liquidation_enabled(
            policy
        )
    ):
        return None
    snapshot = _mapping(
        input_payload.get("execution_snapshot")
    )
    assets = _mapping(snapshot.get("assets"))
    positions = snapshot.get("positions")
    positions = (
        positions
        if isinstance(positions, list)
        else []
    )
    available_crypto = {
        str(position.get("symbol", "")).upper()
        for position in positions
        if isinstance(position, Mapping)
        and _positive_decimal(
            position.get("available_quantity")
        )
        and is_crypto_asset(
            _mapping(
                assets.get(
                    str(
                        position.get(
                            "symbol",
                            "",
                        )
                    ).upper()
                )
            )
        )
        and _mapping(
            assets.get(
                str(
                    position.get(
                        "symbol",
                        "",
                    )
                ).upper()
            )
        ).get("status")
        == "active"
        and _mapping(
            assets.get(
                str(
                    position.get(
                        "symbol",
                        "",
                    )
                ).upper()
            )
        ).get("tradable")
        is True
    }
    if not available_crypto:
        return None

    portfolio = _mapping(
        input_payload.get("portfolio")
    )
    portfolio_decisions = portfolio.get(
        "decisions"
    )
    portfolio_decisions = (
        portfolio_decisions
        if isinstance(
            portfolio_decisions,
            list,
        )
        else []
    )
    decisions: list[dict[str, Any]] = []
    portfolio_symbols: set[str] = set()
    for raw in portfolio_decisions:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(
            raw.get("symbol", "")
        ).upper()
        if not symbol:
            continue
        portfolio_symbols.add(symbol)
        decisions.append(
            {
                "symbol": symbol,
                "portfolio_action": str(
                    raw.get("action", "hold")
                ),
                "execution_decision": "defer",
                "side": "none",
                "target_weight": str(
                    raw.get("target_weight", "0")
                ),
                "maximum_weight": str(
                    raw.get("maximum_weight", "0")
                ),
                "execution_fraction": "0",
                "urgency": "none",
                "price_condition": {
                    "reference": "none",
                    "limit_price": None,
                    "do_not_execute_above": None,
                    "review_below": None,
                },
                "order_intent": {
                    "preferred_type": "none",
                    "time_in_force_preference": "none",
                    "extended_hours_requested": False,
                    "allow_queue": False,
                    "allow_partial_fill": False,
                },
                "decision_reason": (
                    "Deferred until automatic Crypto liquidation is "
                    "broker-confirmed and USD buying power is refreshed."
                ),
                "execution_risks": [
                    "Crypto sale proceeds are not yet confirmed as USD.",
                ],
                "required_checks": [
                    "Refresh cash and buying power after broker reconciliation.",
                ],
                "source_references": [
                    "execution_snapshot",
                    "automatic_crypto_liquidation_policy",
                ],
            }
        )
    for symbol in sorted(
        available_crypto - portfolio_symbols
    ):
        decisions.append(
            {
                "symbol": symbol,
                "portfolio_action": "close",
                "execution_decision": "defer",
                "side": "none",
                "target_weight": "0",
                "maximum_weight": "0",
                "execution_fraction": "0",
                "urgency": "none",
                "price_condition": {
                    "reference": "none",
                    "limit_price": None,
                    "do_not_execute_above": None,
                    "review_below": None,
                },
                "order_intent": {
                    "preferred_type": "none",
                    "time_in_force_preference": "none",
                    "extended_hours_requested": False,
                    "allow_queue": False,
                    "allow_partial_fill": False,
                },
                "decision_reason": (
                    "Detected outside portfolio scope; Python will force "
                    "automatic liquidation."
                ),
                "execution_risks": [],
                "required_checks": [
                    "Revalidate available quantity before submission.",
                ],
                "source_references": [
                    "execution_snapshot",
                    "automatic_crypto_liquidation_policy",
                ],
            }
        )
    generated = datetime.now(timezone.utc)
    valid_minutes = int(
        execution_policy.get("valid_minutes", 30)
    )
    open_orders = snapshot.get("open_orders")
    open_orders = (
        open_orders
        if isinstance(open_orders, list)
        else []
    )
    fallback_protections: list[
        dict[str, Any]
    ] = []
    for position in positions:
        if not isinstance(position, Mapping):
            continue
        symbol = str(
            position.get("symbol", "")
        ).upper()
        asset = _mapping(assets.get(symbol))
        if (
            not symbol
            or is_crypto_asset(asset)
            or str(
                position.get("side", "long")
            ).lower()
            != "long"
        ):
            continue
        try:
            quantity = Decimal(
                str(position.get("quantity", "0"))
            )
            reference = Decimal(
                str(
                    position.get("current_price")
                    or position.get(
                        "average_entry_price"
                    )
                    or "0"
                )
            )
        except (InvalidOperation, ValueError):
            continue
        if quantity <= 0 or reference <= 0:
            continue
        stop = (
            reference * Decimal("0.92")
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_DOWN,
        )
        stop_limit = (
            reference * Decimal("0.915")
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_DOWN,
        )
        fallback_protections.append(
            {
                "symbol": symbol,
                "mode": "stop_limit",
                "apply_to": "existing_position",
                "coverage_fraction": "1",
                "time_in_force": (
                    "day"
                    if quantity
                    != quantity.to_integral_value()
                    else "gtc"
                ),
                "take_profit_price": None,
                "stop_price": format(stop, "f"),
                "stop_limit_price": format(
                    stop_limit,
                    "f",
                ),
                "trail_price": None,
                "trail_percent": None,
                "stages": [],
                "reason": (
                    "Deterministic emergency fallback while Crypto "
                    "liquidation temporarily bypasses Codex."
                ),
            }
        )
    output = {
        "schema_version": "1.0",
        "stage": "execution_decision",
        "profile_id": str(
            profile.get("profile_id", "")
        ),
        "strategy_id": str(
            _mapping(
                input_payload.get("release")
            ).get("strategy_id", "")
        ),
        "strategy_version": str(
            _mapping(
                input_payload.get("release")
            ).get("strategy_version", "")
        ),
        "run_date": str(
            input_payload.get("run_date", "")
        ),
        "cycle_id": str(
            input_payload.get("cycle_id", "")
        ),
        "generated_at": generated.isoformat(),
        "input_signature": str(
            input_payload.get(
                "input_signature",
                "",
            )
        ),
        "status": "success_local_only",
        "network_research": {
            "status": "not_requested",
            "web_access": False,
            "summary": (
                "Skipped Codex execution analysis because deterministic "
                "Live Crypto liquidation has priority."
            ),
            "warnings": [
                "Network research was intentionally skipped for immediate "
                "deterministic liquidation.",
            ],
        },
        "market_assessment": {
            "market_phase": str(
                snapshot.get(
                    "market_phase",
                    "unknown",
                )
            ),
            "summary": (
                "Existing Crypto must be sold before any equity deployment."
            ),
            "key_risks": [
                "Market order execution price may differ from broker valuation.",
                "USD proceeds cannot be reused until reconciliation confirms them.",
            ],
        },
        "review_response": {
            "summary": (
                "Automatic liquidation is a Python capital policy, not a "
                "model recommendation."
            ),
            "honored_prohibitions": [],
            "honored_constraints": [],
            "rejected_requests": [],
            "unresolved_hard_constraints": [],
        },
        "portfolio_response": {
            "summary": (
                "Crypto liquidation is prioritized; all other deployment "
                "waits for refreshed USD."
            ),
            "modified_symbols": [],
            "deferred_symbols": sorted(
                portfolio_symbols
            ),
            "rejected_symbols": [],
        },
        "decisions": decisions,
        "protection_plans": (
            fallback_protections
        ),
        "open_order_actions": [
            {
                "order_reference": (
                    str(
                        order.get(
                            "broker_order_id",
                            "",
                        )
                    )
                    or str(
                        order.get(
                            "client_order_id",
                            "",
                        )
                    )
                    or (
                        str(
                            order.get(
                                "symbol",
                                "UNKNOWN",
                            )
                        )
                        + ":review"
                    )
                ),
                "symbol": str(
                    order.get("symbol", "")
                ).upper(),
                "action": "review",
                "reason": (
                    "Existing order is not changed by automatic Crypto "
                    "liquidation."
                ),
            }
            for order in open_orders
            if isinstance(order, Mapping)
            and order.get("symbol")
        ],
        "requires_portfolio_replan": False,
        "requires_manual_review": False,
        "valid_until": (
            generated
            + timedelta(
                minutes=max(valid_minutes, 1)
            )
        ).isoformat(),
        "warnings": [
            "Codex execution call skipped for immediate Live Crypto liquidation.",
        ],
        "source_references": [
            {
                "id": "execution_snapshot",
                "title": "Current broker execution snapshot",
                "url": "",
                "source_type": "input",
                "retrieved_at": snapshot.get(
                    "retrieved_at"
                ),
            },
            {
                "id": (
                    "automatic_crypto_liquidation_policy"
                ),
                "title": "Automatic Crypto liquidation policy",
                "url": "",
                "source_type": "local",
                "retrieved_at": generated.isoformat(),
            },
        ],
    }
    return _force_live_crypto_liquidations(
        output,
        input_payload=input_payload,
    )


@dataclass(frozen=True)
class ExecutionStageResult:
    action: str
    input_path: Path
    output_path: Path
    validation_path: Path
    input_signature: str
    approve_count: int
    modify_count: int
    defer_count: int
    reject_count: int
    no_action_count: int
    network_status: str
    warnings: tuple[str, ...]
    output: dict[str, Any]
    validation: ExecutionValidationResult
    input_result: ExecutionInputBuildResult


def _neutralize_non_executable_intents(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove residual order intent only from non-executable decisions."""

    result = dict(payload)
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        return result, ()
    decisions: list[object] = []
    normalized_symbols: list[str] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            decisions.append(raw)
            continue
        decision = dict(raw)
        if (
            decision.get("execution_decision")
            not in NON_EXECUTABLE_DECISIONS
        ):
            decisions.append(decision)
            continue
        before = {
            "side": decision.get("side"),
            "execution_fraction": decision.get(
                "execution_fraction"
            ),
            "urgency": decision.get("urgency"),
            "price_condition": decision.get(
                "price_condition"
            ),
            "order_intent": decision.get(
                "order_intent"
            ),
        }
        for key, value in (
            ("side", "none"),
            ("execution_fraction", "0"),
            ("urgency", "none"),
        ):
            if key in decision:
                decision[key] = value
        raw_price = decision.get(
            "price_condition"
        )
        if isinstance(raw_price, dict):
            price = dict(raw_price)
            for key, value in (
                ("reference", "none"),
                ("limit_price", None),
                ("do_not_execute_above", None),
                ("review_below", None),
            ):
                if key in price:
                    price[key] = value
            decision["price_condition"] = price
        raw_intent = decision.get("order_intent")
        if isinstance(raw_intent, dict):
            intent = dict(raw_intent)
            for key, value in (
                ("preferred_type", "none"),
                (
                    "time_in_force_preference",
                    "none",
                ),
                (
                    "extended_hours_requested",
                    False,
                ),
                ("allow_queue", False),
                ("allow_partial_fill", False),
            ):
                if key in intent:
                    intent[key] = value
            decision["order_intent"] = intent
        after = {
            "side": decision.get("side"),
            "execution_fraction": decision.get(
                "execution_fraction"
            ),
            "urgency": decision.get("urgency"),
            "price_condition": decision.get(
                "price_condition"
            ),
            "order_intent": decision.get(
                "order_intent"
            ),
        }
        if after != before:
            normalized_symbols.append(
                str(
                    decision.get(
                        "symbol",
                        "<unknown>",
                    )
                )
            )
        decisions.append(decision)
    result["decisions"] = decisions
    if normalized_symbols:
        raw_warnings = result.get("warnings")
        if isinstance(raw_warnings, list):
            result["warnings"] = [
                *raw_warnings,
                "Python安全归零了非执行决定中的残留订单意图："
                + ",".join(normalized_symbols),
            ]
    return result, tuple(normalized_symbols)


def _defer_without_trade_permission(
    payload: dict[str, Any],
    *,
    input_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Fail safely to defer model approvals when the CLI is dry-run only."""

    permission = _mapping(
        input_payload.get("trade_permission")
    )
    if (
        permission.get("submission_enabled")
        is True
    ):
        return dict(payload), ()
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        return dict(payload), ()
    deferred: list[str] = []
    decisions: list[object] = []
    for raw in raw_decisions:
        if not isinstance(raw, Mapping):
            decisions.append(raw)
            continue
        decision = dict(raw)
        if decision.get(
            "execution_decision"
        ) in {"approve", "modify"}:
            symbol = str(
                decision.get(
                    "symbol",
                    "<unknown>",
                )
            )
            deferred.append(symbol)
            decision[
                "execution_decision"
            ] = "defer"
            decision["decision_reason"] = (
                "Python deferred this intent because "
                "trade_permission.submission_enabled=false; "
                "the protection plan remains available for dry-run validation."
            )
        decisions.append(decision)
    result = dict(payload)
    result["decisions"] = decisions
    if deferred:
        snapshot = _mapping(
            input_payload.get(
                "execution_snapshot"
            )
        )
        assets = _mapping(
            snapshot.get("assets")
        )
        current_equities = {
            str(item.get("symbol", "")).upper()
            for item in (
                snapshot.get("positions")
                if isinstance(
                    snapshot.get("positions"),
                    list,
                )
                else []
            )
            if isinstance(item, Mapping)
            and _positive_decimal(
                item.get("quantity")
            )
            and not is_crypto_asset(
                _mapping(
                    assets.get(
                        str(
                            item.get(
                                "symbol",
                                "",
                            )
                        ).upper()
                    )
                )
            )
        }
        raw_plans = result.get(
            "protection_plans"
        )
        if isinstance(raw_plans, list):
            retained_plans: list[object] = []
            for raw_plan in raw_plans:
                if not isinstance(
                    raw_plan,
                    Mapping,
                ):
                    retained_plans.append(
                        raw_plan
                    )
                    continue
                plan = dict(raw_plan)
                symbol = str(
                    plan.get(
                        "symbol",
                        "",
                    )
                ).upper()
                if symbol not in current_equities:
                    continue
                if plan.get("apply_to") == "both":
                    plan[
                        "apply_to"
                    ] = "existing_position"
                retained_plans.append(plan)
            result[
                "protection_plans"
            ] = retained_plans
        response = dict(
            _mapping(
                result.get(
                    "portfolio_response"
                )
            )
        )
        modified = response.get(
            "modified_symbols"
        )
        response["modified_symbols"] = [
            symbol
            for symbol in (
                modified
                if isinstance(modified, list)
                else []
            )
            if str(symbol) not in set(deferred)
        ]
        previous = response.get(
            "deferred_symbols"
        )
        response["deferred_symbols"] = list(
            dict.fromkeys(
                [
                    *(
                        previous
                        if isinstance(
                            previous,
                            list,
                        )
                        else []
                    ),
                    *deferred,
                ]
            )
        )
        result["portfolio_response"] = response
        warnings = result.get("warnings")
        result["warnings"] = [
            *(
                warnings
                if isinstance(warnings, list)
                else []
            ),
            (
                "Python因本轮未授权交易而把模型执行意图安全改为defer："
                + ",".join(deferred)
            ),
        ]
    return result, tuple(deferred)


def _capability_paths(
    release: StrategyRelease,
) -> tuple[Path, ...]:
    return (
        release.root
        / "prompts"
        / "execution.md",
        release.root
        / "prompts"
        / "execution_AGENTS.md",
        release.root
        / "schemas"
        / "execution_output.schema.json",
        release.root
        / "config"
        / "execution_policy.json",
    )


def _parse_time(value: object) -> float:
    from datetime import datetime, timezone

    if not isinstance(value, str):
        return 0.0
    normalized = value
    if normalized.endswith("Z"):
        normalized = (
            normalized[:-1] + "+00:00"
        )
    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    return parsed.timestamp()


def _validation_document(
    result: ExecutionValidationResult,
    *,
    input_signature: str,
) -> dict[str, Any]:
    payload = result.to_dict()
    payload.update(
        {
            "input_signature": input_signature,
            "validated_at": utc_now_iso(),
        }
    )
    return payload


def _stage_result(
    *,
    paths: CyclePaths,
    output: dict[str, Any],
    validation: ExecutionValidationResult,
    input_result: ExecutionInputBuildResult,
) -> ExecutionStageResult:
    counts = {
        "approve": 0,
        "modify": 0,
        "defer": 0,
        "reject": 0,
        "no_action": 0,
    }
    for item in output.get("decisions", []):
        if not isinstance(item, dict):
            continue
        decision = str(
            item.get(
                "execution_decision",
                "",
            )
        )
        if decision in counts:
            counts[decision] += 1
    network = output.get(
        "network_research",
        {},
    )
    network = (
        network
        if isinstance(network, dict)
        else {}
    )
    warnings = [
        str(value)
        for value in output.get("warnings", [])
    ]
    warnings.extend(
        str(item.get("message", ""))
        for item in validation.warnings
        if item.get("message")
    )
    return ExecutionStageResult(
        action="run",
        input_path=paths.execution_input,
        output_path=paths.execution_output,
        validation_path=(
            paths.execution_validation
        ),
        input_signature=(
            input_result.input_signature
        ),
        approve_count=counts["approve"],
        modify_count=counts["modify"],
        defer_count=counts["defer"],
        reject_count=counts["reject"],
        no_action_count=counts["no_action"],
        network_status=str(
            network.get("status", "unknown")
        ),
        warnings=tuple(warnings),
        output=output,
        validation=validation,
        input_result=input_result,
    )


def execute_execution_decision(
    *,
    paths: CyclePaths,
    state: CycleState,
    config: V2Config,
    runner: ExecutionRunner | None = None,
    release: StrategyRelease | None = None,
) -> ExecutionStageResult:
    """Run Stage E and stop before any order construction."""

    active_release = (
        release
        or load_strategy_release(
            paths.strategy_id,
            paths.strategy_version,
            project_root=paths.project_root,
        )
    )
    missing = [
        str(path)
        for path in _capability_paths(
            active_release
        )
        if not path.is_file()
    ]
    if missing:
        raise SafetyBlockedError(
            "strategy release不包含execution能力",
            code="EXECUTION_CAPABILITY_MISSING",
            details={"missing": missing},
        )
    schema = load_json_object(
        active_release.root
        / "schemas"
        / "execution_output.schema.json"
    )
    preflight_output_schema(schema)
    policy = load_json_object(
        active_release.root
        / "config"
        / "execution_policy.json"
    )
    required_cycle_files = (
        paths.initial_guidance,
        paths.user_review,
        paths.portfolio_output,
        paths.execution_snapshot,
    )
    missing_cycle = [
        str(path)
        for path in required_cycle_files
        if not path.is_file()
    ]
    if missing_cycle:
        raise SafetyBlockedError(
            "Stage E缺少当前cycle必要输入",
            code="EXECUTION_INPUT_MISSING",
            details={"missing": missing_cycle},
        )
    guidance = load_initial_guidance(
        paths
    )
    review = load_user_review(paths)
    portfolio = load_json_object(
        paths.portfolio_output
    )
    snapshot = load_json_object(
        paths.execution_snapshot
    )
    if (
        snapshot.get("profile_id")
        != paths.profile_id
        or snapshot.get("strategy_id")
        != paths.strategy_id
        or snapshot.get("strategy_version")
        != paths.strategy_version
        or snapshot.get("run_date")
        != paths.run_date
        or snapshot.get("cycle_id")
        != paths.cycle_id
    ):
        raise SafetyBlockedError(
            "execution snapshot身份不匹配",
            code="EXECUTION_SNAPSHOT_IDENTITY_MISMATCH",
        )
    if _parse_time(
        snapshot.get("retrieved_at")
    ) <= _parse_time(
        portfolio.get("generated_at")
    ):
        raise SafetyBlockedError(
            "execution snapshot必须晚于portfolio output",
            code="EXECUTION_SNAPSHOT_NOT_FRESHER",
        )
    data_quality = snapshot.get(
        "data_quality",
        {},
    )
    if (
        not isinstance(data_quality, dict)
        or data_quality.get(
            "execution_ready"
        )
        is not True
    ):
        raise SafetyBlockedError(
            "执行级账户或订单数据不完整",
            code="EXECUTION_SNAPSHOT_NOT_READY",
        )
    account = snapshot.get("account")
    if (
        not isinstance(account, dict)
        or account.get("trading_blocked")
        is True
        or account.get("account_blocked")
        is True
        or account.get(
            "trade_suspended_by_user"
        )
        is True
    ):
        raise SafetyBlockedError(
            "账户已阻止交易，不能运行执行代理",
            code="EXECUTION_ACCOUNT_BLOCKED",
        )
    risk_profile = load_risk_profile(
        state.release["risk_profile"],
        project_root=paths.project_root,
    )
    input_result = build_execution_input(
        paths=paths,
        state=state,
        initial_guidance=guidance.to_dict(),
        user_review=review.to_dict(),
        portfolio_output=portfolio,
        execution_snapshot=snapshot,
        risk_profile=risk_profile,
        risk_limits=config.risk,
        execution_policy=policy,
        release=active_release,
    )
    atomic_write_json(
        paths.execution_input,
        input_result.payload,
    )
    automatic = (
        _automatic_crypto_liquidation_output(
            input_result.payload
        )
    )
    if automatic is not None:
        automatic_output, forced_crypto = automatic
        validation = validate_execution_output(
            automatic_output,
            input_payload=input_result.payload,
            schema=schema,
        )
        atomic_write_json(
            paths.execution_codex_call,
            {
                "schema_version": "1.0",
                "stage": "execution_decision",
                "status": "skipped_by_policy",
                "input_signature": (
                    input_result.input_signature
                ),
                "completed_at": utc_now_iso(),
                "attempts": [],
                "reason": (
                    "automatic_live_crypto_liquidation"
                ),
                "safe_normalizations": [
                    {
                        "type": (
                            "force_live_crypto_liquidation"
                        ),
                        "symbol": symbol,
                    }
                    for symbol in forced_crypto
                ],
            },
        )
        atomic_write_json(
            paths.execution_validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
            ),
        )
        if not validation.valid:
            raise CodexOutputValidationError(
                "自动Crypto清仓输出未通过Schema或业务校验",
                details={
                    "error_codes": sorted(
                        {
                            str(item["code"])
                            for item
                            in validation.errors
                        }
                    )
                },
            )
        atomic_write_json(
            paths.execution_output,
            automatic_output,
        )
        return _stage_result(
            paths=paths,
            output=automatic_output,
            validation=validation,
            input_result=input_result,
        )
    workspace = prepare_execution_workspace(
        paths,
        input_payload=input_result.payload,
        release=active_release,
    )
    active_runner = (
        runner
        or ExecutionCodexRunner(
            timeout_seconds=float(
                config.system[
                    "codex_timeout_seconds"
                ]
            ),
            retry_count=int(
                config.system[
                    "codex_retry_count"
                ]
            ),
            **codex_runner_settings(active_release),
        )
    )
    try:
        run_result = active_runner.run(
            workspace
        )
        (
            permission_output,
            permission_deferred,
        ) = _defer_without_trade_permission(
            run_result.payload,
            input_payload=input_result.payload,
        )
        normalized_output, neutralized = (
            _neutralize_non_executable_intents(
                permission_output
            )
        )
        normalized_output, forced_crypto = (
            _force_live_crypto_liquidations(
                normalized_output,
                input_payload=input_result.payload,
            )
        )
        atomic_write_json(
            paths.execution_codex_call,
            {
                **run_result.call_record,
                "input_signature": (
                    input_result.input_signature
                ),
                "safe_normalizations": [
                    {
                        "type": (
                            "defer_without_trade_permission"
                        ),
                        "symbol": symbol,
                    }
                    for symbol in permission_deferred
                ]
                + [
                    {
                        "type": (
                            "neutralize_non_executable_intent"
                        ),
                        "symbol": symbol,
                    }
                    for symbol in neutralized
                ]
                + [
                    {
                        "type": (
                            "force_live_crypto_liquidation"
                        ),
                        "symbol": symbol,
                    }
                    for symbol in forced_crypto
                ],
            },
        )
        validation = validate_execution_output(
            normalized_output,
            input_payload=input_result.payload,
            schema=schema,
        )
        atomic_write_json(
            paths.execution_validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
            ),
        )
        if not validation.valid:
            raise CodexOutputValidationError(
                "Codex execution输出未通过Schema或业务校验",
                details={
                    "error_codes": sorted(
                        {
                            str(item["code"])
                            for item
                            in validation.errors
                        }
                    )
                },
            )
        atomic_write_json(
            paths.execution_output,
            normalized_output,
        )
        return _stage_result(
            paths=paths,
            output=normalized_output,
            validation=validation,
            input_result=input_result,
        )
    except Exception as error:
        if not isinstance(
            error,
            CodexOutputValidationError,
        ):
            record = getattr(
                active_runner,
                "last_call_record",
                None,
            )
            atomic_write_json(
                paths.execution_codex_call,
                record
                if isinstance(record, dict)
                else {
                    "schema_version": "1.0",
                    "stage": "execution_decision",
                    "status": "failed",
                    "input_signature": (
                        input_result.input_signature
                    ),
                    "completed_at": utc_now_iso(),
                    "attempts": [],
                    "error_code": getattr(
                        error,
                        "code",
                        "UNEXPECTED_ERROR",
                    ),
                },
            )
        raise


def run_execution_stage(
    **kwargs: Any,
) -> ExecutionStageResult:
    return execute_execution_decision(**kwargs)
