"""构建并校验 WA Trader v2 Stage E 的执行意图。

作用：把 portfolio、最新执行快照、用户复查、权限与风险配置合成为签名输入，
并验证 approve/modify/defer/reject/no_action 等意图。
重要性：这是战略权重与未来订单构建之间的最后一道研究边界；不得输出最终数量或订单。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from v2.crypto_liquidation import (
    is_automatic_crypto_liquidation_decision,
)
from v2.models.state import CycleState
from v2.profiles import RiskProfile
from v2.releases import StrategyRelease, sha256_file
from v2.runtime import CyclePaths, utc_now_iso


ZERO = Decimal("0")
EXECUTION_FORBIDDEN_OUTPUT_FIELDS = {
    "quantity",
    "qty",
    "shares",
    "notional",
    "dollar_amount",
    "final_order",
    "broker_order_request",
    "submitted",
    "filled",
}
EXECUTABLE_DECISIONS = {
    "approve",
    "modify",
}
NON_EXECUTABLE_DECISIONS = {
    "defer",
    "reject",
    "no_action",
}
CLOSED_EQUITY_PHASES = {
    "market_closed_weekend",
    "market_closed_holiday",
}


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    result = Decimal(str(value))
    if not result.is_finite():
        raise InvalidOperation
    return result


def _decimal_or_zero(value: object) -> Decimal:
    try:
        return _decimal(value)
    except (InvalidOperation, ValueError):
        return ZERO


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    return parsed.astimezone(timezone.utc)


def _issue(
    code: str,
    message: str,
    path: str = "$",
) -> dict[str, str]:
    return {
        "code": code,
        "message": message,
        "path": path,
    }


@dataclass(frozen=True)
class ExecutionPriceCondition:
    reference: str
    limit_price: Decimal | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionOrderIntent:
    preferred_type: str
    extended_hours_requested: bool
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionDecision:
    symbol: str
    execution_decision: str
    target_weight: Decimal
    execution_fraction: Decimal
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionInput:
    payload: Mapping[str, Any]
    input_signature: str


@dataclass(frozen=True)
class ExecutionOutput:
    decisions: tuple[ExecutionDecision, ...]
    requires_portfolio_replan: bool
    requires_manual_review: bool
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionInputBuildResult:
    payload: dict[str, Any]
    input_signature: str
    portfolio_hash: str
    snapshot_hash: str
    review_hash: str


@dataclass(frozen=True)
class ExecutionValidationResult:
    valid: bool
    schema_valid: bool
    business_valid: bool
    errors: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "stage": "execution_decision",
            "valid": self.valid,
            "schema_valid": self.schema_valid,
            "business_valid": self.business_valid,
            "errors": [
                dict(item) for item in self.errors
            ],
            "warnings": [
                dict(item) for item in self.warnings
            ],
        }


def effective_execution_limits(
    risk_profile: RiskProfile,
    risk_limits: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay profile risk settings on shared execution mechanics."""

    effective = dict(risk_limits)
    settings = risk_profile.settings
    aliases = {
        "maximum_single_position_weight": (
            "max_single_symbol_weight"
        ),
        "maximum_sector_weight": (
            "max_sector_weight"
        ),
        "minimum_cash_weight": (
            "minimum_cash_weight"
        ),
        "minimum_order_value": (
            "minimum_order_value"
        ),
        "quote_max_age_seconds": (
            "max_quote_age_seconds"
        ),
        "closed_session_quote_max_age_seconds": (
            "max_closed_session_quote_age_seconds"
        ),
        "allow_short_positions": (
            "allow_short"
        ),
        "regular_spread_limit_bps": (
            "max_slippage_bps"
        ),
        "extended_spread_limit_bps": (
            "max_extended_hours_spread_bps"
        ),
    }
    for profile_key, execution_key in aliases.items():
        if profile_key in settings:
            effective[execution_key] = settings[
                profile_key
            ]
    effective["risk_profile_reference"] = (
        risk_profile.reference
    )
    return effective


def build_execution_input(
    *,
    paths: CyclePaths,
    state: CycleState,
    initial_guidance: Mapping[str, Any],
    user_review: Mapping[str, Any],
    portfolio_output: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
    risk_profile: RiskProfile,
    risk_limits: Mapping[str, Any],
    execution_policy: Mapping[str, Any],
    release: StrategyRelease,
) -> ExecutionInputBuildResult:
    """Build a signed, cycle-local Stage E input from refreshed facts."""

    portfolio_hash = _hash(
        portfolio_output
    )
    snapshot_hash = _hash(
        execution_snapshot
    )
    review_hash = str(
        user_review.get(
            "review_hash",
            _hash(user_review),
        )
    )
    release_payload = {
        "strategy_id": release.strategy_id,
        "strategy_version": (
            release.strategy_version
        ),
        "release_hash": release.release_hash,
        "risk_profile": risk_profile.reference,
        "risk_profile_hash": sha256_file(
            risk_profile.source_path
        ),
    }
    artifacts = {
        "prompt_hash": release.prompt_hashes.get(
            "prompts/execution.md",
            "",
        ),
        "agents_hash": release.prompt_hashes.get(
            "prompts/execution_AGENTS.md",
            "",
        ),
        "schema_hash": release.schema_hashes.get(
            "schemas/execution_output.schema.json",
            "",
        ),
        "policy_hash": release.config_hashes.get(
            "config/execution_policy.json",
            "",
        ),
        "codex_policy_hash": (
            release.config_hashes.get(
                "config/codex_policy.json",
                "",
            )
        ),
    }
    signature_payload = {
        "profile_id": paths.profile_id,
        "strategy_id": paths.strategy_id,
        "strategy_version": paths.strategy_version,
        "risk_profile": risk_profile.reference,
        "risk_profile_hash": (
            release_payload["risk_profile_hash"]
        ),
        "guidance_hash": initial_guidance.get(
            "guidance_hash",
            "",
        ),
        "review_hash": review_hash,
        "portfolio_hash": portfolio_hash,
        "snapshot_hash": snapshot_hash,
        "trade_permission": (
            state.trade_permission.to_dict()
        ),
        "execution_artifacts": artifacts,
    }
    input_signature = _hash(
        signature_payload
    )
    raw_comment = str(
        user_review.get(
            "raw_comment",
            "",
        )
    ).strip()
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "execution_decision",
        "profile": {
            "profile_id": paths.profile_id,
            "environment": risk_profile.environment,
        },
        "release": release_payload,
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "generated_at": utc_now_iso(),
        "input_signature": input_signature,
        "input_components": signature_payload,
        "trade_permission": (
            state.trade_permission.to_dict()
        ),
        "initial_guidance": dict(
            initial_guidance
        ),
        "user_review": dict(user_review),
        "review_analysis": {
            "has_raw_comment": bool(raw_comment),
            "requires_conservative_defer": (
                bool(raw_comment)
            ),
            "structured_prohibitions": list(
                user_review.get(
                    "prohibitions",
                    [],
                )
            ),
            "structured_constraints": list(
                user_review.get(
                    "constraints",
                    [],
                )
            ),
        },
        "portfolio": dict(portfolio_output),
        "execution_snapshot": dict(
            execution_snapshot
        ),
        "risk_profile": {
            "reference": risk_profile.reference,
            "settings": dict(
                risk_profile.settings
            ),
            "execution_limits": (
                effective_execution_limits(
                    risk_profile,
                    risk_limits,
                )
            ),
        },
        "execution_policy": dict(
            execution_policy
        ),
        "data_quality": dict(
            execution_snapshot.get(
                "data_quality",
                {},
            )
        )
        if isinstance(
            execution_snapshot.get(
                "data_quality"
            ),
            Mapping,
        )
        else {},
    }
    return ExecutionInputBuildResult(
        payload=payload,
        input_signature=input_signature,
        portfolio_hash=portfolio_hash,
        snapshot_hash=snapshot_hash,
        review_hash=review_hash,
    )


def _forbidden_paths(
    node: object,
    path: str = "$",
) -> list[str]:
    result: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}"
            if (
                key
                in EXECUTION_FORBIDDEN_OUTPUT_FIELDS
            ):
                result.append(child)
            result.extend(
                _forbidden_paths(value, child)
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            result.extend(
                _forbidden_paths(
                    value,
                    f"{path}[{index}]",
                )
            )
    return result


def _structured_prohibits(
    prohibitions: list[str],
    symbol: str,
) -> bool:
    upper = symbol.upper()
    for value in prohibitions:
        normalized = str(value).strip().upper()
        if (
            normalized in {
                upper,
                "ALL",
                "ALL_BUYS",
                "NO_BUY",
                "全部",
                "禁止买入",
            }
            or upper in normalized
        ):
            return True
    return False


def _protection_issues(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    decisions: list[object],
) -> list[dict[str, str]]:
    """Validate Codex protection choices before Stage F can size them."""

    issues: list[dict[str, str]] = []
    policy = input_payload.get(
        "execution_policy"
    )
    policy = (
        policy
        if isinstance(policy, Mapping)
        else {}
    )
    settings = policy.get(
        "position_protection"
    )
    settings = (
        settings
        if isinstance(settings, Mapping)
        else {}
    )
    if settings.get("enabled") is not True:
        return issues
    plans = payload.get("protection_plans")
    plans = plans if isinstance(plans, list) else []
    raw_positions = snapshot.get("positions")
    positions = {
        str(item.get("symbol", "")).upper(): item
        for item in (
            raw_positions
            if isinstance(raw_positions, list)
            else []
        )
        if isinstance(item, Mapping)
        and item.get("symbol")
    }
    assets = snapshot.get("assets")
    assets = (
        assets
        if isinstance(assets, Mapping)
        else {}
    )
    quotes = snapshot.get("quotes")
    quotes = (
        quotes
        if isinstance(quotes, Mapping)
        else {}
    )
    decision_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in decisions
        if isinstance(item, Mapping)
        and item.get("symbol")
    }
    entry_symbols = {
        symbol
        for symbol, item in decision_by_symbol.items()
        if item.get("execution_decision")
        in EXECUTABLE_DECISIONS
        and item.get("side") == "buy"
        and item.get("portfolio_action")
        in {"open", "increase"}
    }
    long_symbols = {
        symbol
        for symbol, position in positions.items()
        if str(position.get("side", "long")).lower()
        == "long"
        and _decimal_or_zero(
            position.get("quantity")
        )
        > ZERO
        and (
            not isinstance(
                assets.get(symbol),
                Mapping,
            )
            or assets[symbol].get("asset_class")
            != "crypto"
        )
    }
    seen: set[str] = set()
    plan_by_symbol: dict[
        str,
        Mapping[str, Any],
    ] = {}
    allowed_modes = set(
        str(item)
        for item in settings.get(
            "allow_modes",
            [],
        )
    )
    for index, raw in enumerate(plans):
        if not isinstance(raw, Mapping):
            continue
        path = f"$.protection_plans[{index}]"
        symbol = str(
            raw.get("symbol", "")
        ).upper()
        if symbol in seen:
            issues.append(
                _issue(
                    "DUPLICATE_PROTECTION_SYMBOL",
                    f"{symbol}保护计划重复",
                    path,
                )
            )
        seen.add(symbol)
        plan_by_symbol[symbol] = raw
        if (
            symbol not in long_symbols
            and symbol not in entry_symbols
        ):
            issues.append(
                _issue(
                    "PROTECTION_SYMBOL_OUT_OF_SCOPE",
                    f"{symbol}既不是当前多头也不是本轮新入场",
                    f"{path}.symbol",
                )
            )
        mode = str(raw.get("mode", ""))
        if mode != "none" and mode not in allowed_modes:
            issues.append(
                _issue(
                    "PROTECTION_MODE_NOT_ALLOWED",
                    f"{symbol}保护模式未获policy授权",
                    f"{path}.mode",
                )
            )
        apply_to = str(raw.get("apply_to", ""))
        if (
            symbol in long_symbols
            and symbol not in entry_symbols
            and apply_to == "new_entry"
        ):
            issues.append(
                _issue(
                    "PROTECTION_APPLY_TARGET_INVALID",
                    f"{symbol}只有既有持仓，不能只保护new_entry",
                    f"{path}.apply_to",
                )
            )
        if (
            symbol in entry_symbols
            and symbol not in long_symbols
            and apply_to == "existing_position"
        ):
            issues.append(
                _issue(
                    "PROTECTION_APPLY_TARGET_INVALID",
                    f"{symbol}只有新入场，不能只保护existing_position",
                    f"{path}.apply_to",
                )
            )
        try:
            coverage = _decimal(
                raw.get("coverage_fraction")
            )
        except (InvalidOperation, ValueError):
            coverage = Decimal("-1")
        minimum_coverage = _decimal_or_zero(
            settings.get(
                "minimum_coverage_fraction"
            )
        )
        maximum_coverage = _decimal_or_zero(
            settings.get(
                "maximum_coverage_fraction"
            )
        )
        if mode == "none":
            if coverage != ZERO:
                issues.append(
                    _issue(
                        "PROTECTION_NONE_COVERAGE_INVALID",
                        f"{symbol} mode=none时coverage必须为0",
                        f"{path}.coverage_fraction",
                    )
                )
        elif (
            coverage < minimum_coverage
            or coverage > maximum_coverage
        ):
            issues.append(
                _issue(
                    "PROTECTION_COVERAGE_INVALID",
                    f"{symbol}保护覆盖比例越界",
                    f"{path}.coverage_fraction",
                )
            )
        numeric: dict[str, Decimal | None] = {}
        for field in (
            "take_profit_price",
            "stop_price",
            "stop_limit_price",
            "trail_price",
            "trail_percent",
        ):
            value = raw.get(field)
            if value is None:
                numeric[field] = None
                continue
            try:
                parsed = _decimal(value)
            except (InvalidOperation, ValueError):
                parsed = Decimal("-1")
            numeric[field] = parsed
            if parsed <= ZERO:
                issues.append(
                    _issue(
                        "PROTECTION_PRICE_INVALID",
                        f"{symbol} {field}必须大于0",
                        f"{path}.{field}",
                    )
                )
        required_fields = {
            "stop": ("stop_price",),
            "stop_limit": (
                "stop_price",
                "stop_limit_price",
            ),
            "take_profit": (
                "take_profit_price",
            ),
            "trailing_stop": (),
            "oco": (
                "take_profit_price",
                "stop_price",
            ),
            "bracket": (
                "take_profit_price",
                "stop_price",
            ),
            "oto_stop": ("stop_price",),
            "oto_take_profit": (
                "take_profit_price",
            ),
        }
        for field in required_fields.get(
            mode,
            (),
        ):
            if numeric.get(field) is None:
                issues.append(
                    _issue(
                        "PROTECTION_FIELD_MISSING",
                        f"{symbol} {mode}缺少{field}",
                        f"{path}.{field}",
                    )
                )
        allowed_numeric_fields = {
            "none": set(),
            "stop": {"stop_price"},
            "stop_limit": {
                "stop_price",
                "stop_limit_price",
            },
            "take_profit": {
                "take_profit_price",
            },
            "trailing_stop": {
                "trail_price",
                "trail_percent",
            },
            "oco": {
                "take_profit_price",
                "stop_price",
                "stop_limit_price",
            },
            "bracket": {
                "take_profit_price",
                "stop_price",
                "stop_limit_price",
            },
            "oto_stop": {
                "stop_price",
                "stop_limit_price",
            },
            "oto_take_profit": {
                "take_profit_price",
            },
            "staged_oco": set(),
        }.get(mode, set())
        for field, value in numeric.items():
            if (
                value is not None
                and field
                not in allowed_numeric_fields
            ):
                issues.append(
                    _issue(
                        "UNEXPECTED_PROTECTION_FIELD",
                        f"{symbol} {mode}不得设置{field}",
                        f"{path}.{field}",
                    )
                )
        if mode == "trailing_stop":
            if (
                (numeric["trail_price"] is None)
                == (
                    numeric[
                        "trail_percent"
                    ]
                    is None
                )
            ):
                issues.append(
                    _issue(
                        "TRAIL_CONFIGURATION_INVALID",
                        f"{symbol}移动止损必须且只能设置trail_price或trail_percent",
                        path,
                    )
                )
            trail_percent = numeric[
                "trail_percent"
            ]
            if trail_percent is not None and (
                trail_percent
                < _decimal_or_zero(
                    settings.get(
                        "minimum_trail_percent"
                    )
                )
                or trail_percent
                > _decimal_or_zero(
                    settings.get(
                        "maximum_trail_percent"
                    )
                )
            ):
                issues.append(
                    _issue(
                        "TRAIL_PERCENT_OUT_OF_RANGE",
                        f"{symbol}移动止损百分比越界",
                        f"{path}.trail_percent",
                    )
                )
        stages = raw.get("stages")
        stages = (
            stages
            if isinstance(stages, list)
            else []
        )
        if mode == "staged_oco":
            if (
                not stages
                or len(stages)
                > int(
                    settings.get(
                        "maximum_stages",
                        5,
                    )
                )
            ):
                issues.append(
                    _issue(
                        "PROTECTION_STAGES_INVALID",
                        f"{symbol}分级OCO阶段数量无效",
                        f"{path}.stages",
                    )
                )
            stage_total = ZERO
            for stage_index, stage in enumerate(
                stages
            ):
                if not isinstance(stage, Mapping):
                    continue
                stage_path = (
                    f"{path}.stages[{stage_index}]"
                )
                fraction = _decimal_or_zero(
                    stage.get(
                        "coverage_fraction"
                    )
                )
                stage_total += fraction
                take_profit = _decimal_or_zero(
                    stage.get(
                        "take_profit_price"
                    )
                )
                stop = _decimal_or_zero(
                    stage.get("stop_price")
                )
                stop_limit = (
                    _decimal_or_zero(
                        stage.get(
                            "stop_limit_price"
                        )
                    )
                    if stage.get(
                        "stop_limit_price"
                    )
                    is not None
                    else None
                )
                if (
                    fraction <= ZERO
                    or take_profit <= ZERO
                    or stop <= ZERO
                    or (
                        stop_limit is not None
                        and stop_limit > stop
                    )
                ):
                    issues.append(
                        _issue(
                            "PROTECTION_STAGE_INVALID",
                            f"{symbol}分级OCO参数无效",
                            stage_path,
                        )
                    )
            if stage_total != coverage:
                issues.append(
                    _issue(
                        "PROTECTION_STAGE_COVERAGE_MISMATCH",
                        f"{symbol}分级覆盖合计必须等于总覆盖比例",
                        f"{path}.stages",
                    )
                )
        elif stages:
            issues.append(
                _issue(
                    "UNEXPECTED_PROTECTION_STAGES",
                    f"{symbol}非staged_oco不得包含stages",
                    f"{path}.stages",
                )
            )
        position = positions.get(symbol, {})
        quote = quotes.get(symbol, {})
        quote = (
            quote
            if isinstance(quote, Mapping)
            else {}
        )
        decision = decision_by_symbol.get(
            symbol,
            {},
        )
        price_condition = decision.get(
            "price_condition",
        )
        price_condition = (
            price_condition
            if isinstance(
                price_condition,
                Mapping,
            )
            else {}
        )
        reference = next(
            (
                value
                for value in (
                    _decimal_or_zero(
                        position.get(
                            "current_price"
                        )
                    ),
                    _decimal_or_zero(
                        quote.get("midpoint")
                    ),
                    _decimal_or_zero(
                        price_condition.get(
                            "limit_price"
                        )
                    ),
                    _decimal_or_zero(
                        position.get(
                            "average_entry_price"
                        )
                    ),
                )
                if value > ZERO
            ),
            ZERO,
        )
        take_profit = numeric[
            "take_profit_price"
        ]
        stop = numeric["stop_price"]
        stop_limit = numeric[
            "stop_limit_price"
        ]
        if (
            reference > ZERO
            and take_profit is not None
            and take_profit <= reference
        ):
            issues.append(
                _issue(
                    "TAKE_PROFIT_NOT_ABOVE_REFERENCE",
                    f"{symbol}多头止盈必须高于当前参考价",
                    f"{path}.take_profit_price",
                )
            )
        minimum_stop_distance = (
            _decimal_or_zero(
                settings.get(
                    "minimum_stop_distance_fraction"
                )
            )
        )
        maximum_stop_distance = (
            _decimal_or_zero(
                settings.get(
                    "maximum_stop_distance_fraction"
                )
            )
        )
        minimum_take_profit_distance = (
            _decimal_or_zero(
                settings.get(
                    "minimum_take_profit_distance_fraction"
                )
            )
        )
        maximum_take_profit_distance = (
            _decimal_or_zero(
                settings.get(
                    "maximum_take_profit_distance_fraction"
                )
            )
        )
        if (
            reference > ZERO
            and stop is not None
            and stop < reference
        ):
            distance = (
                reference - stop
            ) / reference
            if (
                distance
                < minimum_stop_distance
                or distance
                > maximum_stop_distance
            ):
                issues.append(
                    _issue(
                        "STOP_DISTANCE_OUT_OF_RANGE",
                        f"{symbol}止损距离越过policy范围",
                        f"{path}.stop_price",
                    )
                )
        if (
            reference > ZERO
            and take_profit is not None
            and take_profit > reference
        ):
            distance = (
                take_profit - reference
            ) / reference
            if (
                distance
                < minimum_take_profit_distance
                or distance
                > maximum_take_profit_distance
            ):
                issues.append(
                    _issue(
                        "TAKE_PROFIT_DISTANCE_OUT_OF_RANGE",
                        f"{symbol}止盈距离越过policy范围",
                        f"{path}.take_profit_price",
                    )
                )
        if (
            reference > ZERO
            and mode == "staged_oco"
        ):
            for stage_index, stage in enumerate(
                stages
            ):
                if not isinstance(stage, Mapping):
                    continue
                stage_take_profit = (
                    _decimal_or_zero(
                        stage.get(
                            "take_profit_price"
                        )
                    )
                )
                stage_stop = _decimal_or_zero(
                    stage.get("stop_price")
                )
                if (
                    stage_take_profit
                    <= reference
                    or stage_stop
                    >= reference
                ):
                    issues.append(
                        _issue(
                            "PROTECTION_STAGE_PRICE_RELATION_INVALID",
                            f"{symbol}分级OCO必须满足止盈高于且止损低于参考价",
                            f"{path}.stages[{stage_index}]",
                        )
                    )
        if (
            reference > ZERO
            and stop is not None
            and stop >= reference
        ):
            issues.append(
                _issue(
                    "STOP_NOT_BELOW_REFERENCE",
                    f"{symbol}多头止损必须低于当前参考价",
                    f"{path}.stop_price",
                )
            )
        if (
            stop is not None
            and stop_limit is not None
            and stop_limit > stop
        ):
            issues.append(
                _issue(
                    "STOP_LIMIT_RELATION_INVALID",
                    f"{symbol}卖出stop-limit的limit不得高于stop",
                    f"{path}.stop_limit_price",
                )
            )
        quantity = _decimal_or_zero(
            position.get("quantity")
        )
        if (
            raw.get("mode") != "none"
            and quantity > ZERO
            and quantity
            != quantity.to_integral_value()
            and raw.get("time_in_force") != "day"
        ):
            issues.append(
                _issue(
                    "FRACTIONAL_PROTECTION_TIF_INVALID",
                    f"{symbol}碎股保护单必须使用day",
                    f"{path}.time_in_force",
                )
            )
    required = set()
    if settings.get(
        "require_plan_for_existing_long_equity"
    ) is True:
        required.update(long_symbols)
    if settings.get(
        "require_plan_for_approved_entry"
    ) is True:
        required.update(entry_symbols)
    for symbol in sorted(required):
        plan = plan_by_symbol.get(symbol)
        if plan is None:
            issues.append(
                _issue(
                    "PROTECTION_PLAN_MISSING",
                    f"{symbol}缺少必须的止盈止损计划",
                    "$.protection_plans",
                )
            )
        elif (
            plan.get("mode") == "none"
            and settings.get(
                "allow_none_when_thesis_based"
            )
            is not True
            and (
                _decimal_or_zero(
                    positions.get(
                        symbol,
                        {},
                    ).get("current_price")
                )
                > ZERO
                or symbol in entry_symbols
            )
        ):
            issues.append(
                _issue(
                    "PROTECTION_NONE_NOT_ALLOWED",
                    f"{symbol}具备定价事实时不得取消全部保护",
                    "$.protection_plans",
                )
            )
    return issues


def validate_execution_output(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    now: datetime | None = None,
) -> ExecutionValidationResult:
    """Apply strict schema, session, review, quote, spread and scope checks."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    validator = Draft202012Validator(schema)
    schema_errors = sorted(
        validator.iter_errors(payload),
        key=lambda item: (
            list(item.absolute_path),
            item.message,
        ),
    )
    for error in schema_errors:
        path = "$"
        for part in error.absolute_path:
            path += (
                f"[{part}]"
                if isinstance(part, int)
                else f".{part}"
            )
        errors.append(
            _issue(
                "SCHEMA_VALIDATION_FAILED",
                error.message,
                path,
            )
        )

    profile = input_payload.get("profile")
    profile = (
        profile
        if isinstance(profile, Mapping)
        else {}
    )
    release = input_payload.get("release")
    release = (
        release
        if isinstance(release, Mapping)
        else {}
    )
    expected = {
        "stage": "execution_decision",
        "profile_id": profile.get(
            "profile_id"
        ),
        "strategy_id": release.get(
            "strategy_id"
        ),
        "strategy_version": release.get(
            "strategy_version"
        ),
        "run_date": input_payload.get(
            "run_date"
        ),
        "cycle_id": input_payload.get(
            "cycle_id"
        ),
        "input_signature": input_payload.get(
            "input_signature"
        ),
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            errors.append(
                _issue(
                    f"{field.upper()}_MISMATCH",
                    f"{field}与execution输入不一致",
                    f"$.{field}",
                )
            )
    if payload.get("status") not in {
        "success",
        "success_local_only",
    }:
        errors.append(
            _issue(
                "INVALID_STATUS",
                "execution status无效",
                "$.status",
            )
        )
    generated_at = _parse_datetime(
        payload.get("generated_at")
    )
    valid_until = _parse_datetime(
        payload.get("valid_until")
    )
    current = now
    if current is not None:
        if current.tzinfo is None:
            current = current.replace(
                tzinfo=timezone.utc
            )
        current = current.astimezone(
            timezone.utc
        )
    if (
        generated_at is None
        or valid_until is None
        or valid_until <= generated_at
    ):
        errors.append(
            _issue(
                "INVALID_VALIDITY_WINDOW",
                "valid_until必须晚于generated_at",
                "$.valid_until",
            )
        )
    else:
        if (
            current is not None
            and valid_until <= current
        ):
            errors.append(
                _issue(
                    "EXECUTION_EXPIRED",
                    "execution意图已过期",
                    "$.valid_until",
                )
            )
        valid_minutes = _decimal_or_zero(
            input_payload.get(
                "execution_policy",
                {},
            ).get("valid_minutes")
            if isinstance(
                input_payload.get(
                    "execution_policy"
                ),
                Mapping,
            )
            else None
        )
        actual_minutes = Decimal(
            str(
                (
                    valid_until - generated_at
                ).total_seconds()
                / 60
            )
        )
        if (
            valid_minutes > ZERO
            and actual_minutes > valid_minutes
        ):
            errors.append(
                _issue(
                    "VALIDITY_WINDOW_EXCEEDS_POLICY",
                    "execution意图有效期超过policy",
                    "$.valid_until",
                )
            )
    portfolio = input_payload.get("portfolio")
    portfolio = (
        portfolio
        if isinstance(portfolio, Mapping)
        else {}
    )
    portfolio_decisions = portfolio.get(
        "decisions",
        [],
    )
    portfolio_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in portfolio_decisions
        if isinstance(item, Mapping)
    } if isinstance(
        portfolio_decisions,
        list,
    ) else {}
    snapshot = input_payload.get(
        "execution_snapshot"
    )
    snapshot = (
        snapshot
        if isinstance(snapshot, Mapping)
        else {}
    )
    market_phase = str(
        snapshot.get(
            "market_phase",
            "unknown",
        )
    )
    market_assessment = payload.get(
        "market_assessment"
    )
    if (
        not isinstance(
            market_assessment,
            Mapping,
        )
        or market_assessment.get(
            "market_phase"
        )
        != market_phase
    ):
        errors.append(
            _issue(
                "MARKET_PHASE_MISMATCH",
                "market assessment与执行快照时段不一致",
                "$.market_assessment.market_phase",
            )
        )
    quotes = snapshot.get("quotes")
    quotes = (
        quotes
        if isinstance(quotes, Mapping)
        else {}
    )
    trades = snapshot.get("latest_trades")
    trades = (
        trades
        if isinstance(trades, Mapping)
        else {}
    )
    assets = snapshot.get("assets")
    assets = (
        assets
        if isinstance(assets, Mapping)
        else {}
    )
    raw_positions = snapshot.get("positions")
    positions_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in (
            raw_positions
            if isinstance(raw_positions, list)
            else []
        )
        if isinstance(item, Mapping)
        and item.get("symbol")
    }
    capability = snapshot.get(
        "broker_extended_hours_capability"
    )
    capability = (
        capability
        if isinstance(capability, Mapping)
        else {}
    )
    risk_profile = input_payload.get(
        "risk_profile"
    )
    risk_profile = (
        risk_profile
        if isinstance(risk_profile, Mapping)
        else {}
    )
    limits = risk_profile.get(
        "execution_limits"
    )
    limits = (
        limits
        if isinstance(limits, Mapping)
        else {}
    )
    policy = input_payload.get(
        "execution_policy"
    )
    policy = (
        policy
        if isinstance(policy, Mapping)
        else {}
    )
    profile_payload = input_payload.get("profile")
    profile_payload = (
        profile_payload
        if isinstance(profile_payload, Mapping)
        else {}
    )
    profile_environment = str(
        profile_payload.get("environment", "")
    )
    adjustment = policy.get(
        "target_weight_adjustment"
    )
    adjustment = (
        adjustment
        if isinstance(adjustment, Mapping)
        else {}
    )
    maximum_absolute = _decimal_or_zero(
        adjustment.get(
            "maximum_absolute_change"
        )
    )
    maximum_relative = _decimal_or_zero(
        adjustment.get(
            "maximum_relative_change"
        )
    )
    fraction_policy = policy.get(
        "execution_fraction"
    )
    fraction_policy = (
        fraction_policy
        if isinstance(
            fraction_policy,
            Mapping,
        )
        else {}
    )
    minimum_fraction = _decimal_or_zero(
        fraction_policy.get("minimum")
    )
    maximum_fraction = _decimal_or_zero(
        fraction_policy.get("maximum")
    )
    closed_policy = policy.get(
        "closed_session_queue"
    )
    closed_policy = (
        closed_policy
        if isinstance(closed_policy, Mapping)
        else {}
    )
    crypto_liquidation_policy = policy.get(
        "crypto_liquidation"
    )
    crypto_liquidation_policy = (
        crypto_liquidation_policy
        if isinstance(
            crypto_liquidation_policy,
            Mapping,
        )
        else {}
    )
    closed_open_fraction = _decimal_or_zero(
        closed_policy.get(
            "maximum_open_execution_fraction"
        )
    )
    closed_reduce_fraction = _decimal_or_zero(
        closed_policy.get(
            "maximum_reduce_execution_fraction"
        )
    )
    quote_age_limit = _decimal_or_zero(
        limits.get("max_quote_age_seconds")
    )
    closed_quote_age_limit = _decimal_or_zero(
        limits.get(
            "max_closed_session_quote_age_seconds",
            closed_policy.get(
                "quote_max_age_seconds"
            ),
        )
    )
    regular_spread_limit = _decimal_or_zero(
        limits.get("max_slippage_bps")
    )
    extended_spread_limit = (
        _decimal_or_zero(
            limits.get(
                "max_extended_hours_spread_bps"
            )
        )
    )
    review_analysis = input_payload.get(
        "review_analysis"
    )
    review_analysis = (
        review_analysis
        if isinstance(
            review_analysis,
            Mapping,
        )
        else {}
    )
    raw_prohibitions = review_analysis.get(
        "structured_prohibitions",
        [],
    )
    prohibitions = [
        str(value)
        for value in raw_prohibitions
    ] if isinstance(
        raw_prohibitions,
        list,
    ) else []
    conservative_defer = (
        review_analysis.get(
            "requires_conservative_defer"
        )
        is True
    )
    requires_replan = (
        payload.get(
            "requires_portfolio_replan"
        )
        is True
    )
    requires_manual = (
        payload.get(
            "requires_manual_review"
        )
        is True
    )
    review_response = payload.get(
        "review_response"
    )
    review_response = (
        review_response
        if isinstance(review_response, Mapping)
        else {}
    )
    unresolved = review_response.get(
        "unresolved_hard_constraints",
        [],
    )
    unresolved = (
        unresolved
        if isinstance(unresolved, list)
        else []
    )
    if conservative_defer:
        if not requires_manual or not unresolved:
            errors.append(
                _issue(
                    "UNRESOLVED_REVIEW_NOT_ESCALATED",
                    "未解析人工评论必须要求manual review",
                    "$.requires_manual_review",
                )
            )

    decisions = payload.get("decisions")
    decisions = (
        decisions
        if isinstance(decisions, list)
        else []
    )
    seen: list[str] = []
    adjustment_breached = False
    trade_permission = input_payload.get(
        "trade_permission"
    )
    trade_permission = (
        trade_permission
        if isinstance(
            trade_permission,
            Mapping,
        )
        else {}
    )
    permission_enabled = (
        trade_permission.get(
            "submission_enabled"
        )
        is True
    )
    for index, raw in enumerate(decisions):
        if not isinstance(raw, Mapping):
            continue
        path = f"$.decisions[{index}]"
        symbol = str(
            raw.get("symbol", "")
        ).upper()
        seen.append(symbol)
        source = portfolio_by_symbol.get(
            symbol
        )
        decision = str(
            raw.get(
                "execution_decision",
                "",
            )
        )
        side = str(raw.get("side", ""))
        portfolio_action = str(
            raw.get("portfolio_action", "")
        )
        numeric_values: dict[
            str,
            Decimal,
        ] = {}
        for field in (
            "target_weight",
            "maximum_weight",
            "execution_fraction",
        ):
            try:
                numeric_values[field] = (
                    _decimal(raw.get(field))
                )
            except (
                InvalidOperation,
                ValueError,
            ):
                numeric_values[field] = ZERO
                errors.append(
                    _issue(
                        "INVALID_EXECUTION_NUMBER",
                        f"{symbol}的{field}不是有限数字",
                        f"{path}.{field}",
                    )
                )
        target = numeric_values[
            "target_weight"
        ]
        maximum = numeric_values[
            "maximum_weight"
        ]
        fraction = numeric_values[
            "execution_fraction"
        ]
        asset = assets.get(symbol)
        asset = (
            asset
            if isinstance(asset, Mapping)
            else {}
        )
        position = positions_by_symbol.get(symbol)
        position = (
            position
            if isinstance(position, Mapping)
            else {}
        )
        automatic_crypto_liquidation = (
            profile_environment == "live"
            and (
                is_automatic_crypto_liquidation_decision(
                    raw,
                    asset,
                    crypto_liquidation_policy,
                )
            )
        )
        if (
            source is None
            and not (
                automatic_crypto_liquidation
                and position
            )
        ):
            errors.append(
                _issue(
                    "SYMBOL_OUTSIDE_PORTFOLIO",
                    f"{symbol}不在portfolio decisions中",
                    f"{path}.symbol",
                )
            )
            source_target = ZERO
        elif source is None:
            source_target = ZERO
        else:
            source_target = _decimal_or_zero(
                source.get("target_weight")
            )
            source_maximum = (
                _decimal_or_zero(
                    source.get(
                        "maximum_weight"
                    )
                )
            )
            configured_maximum = (
                _decimal_or_zero(
                    risk_profile.get(
                        "settings",
                        {},
                    ).get(
                        "maximum_single_position_weight"
                    )
                    if isinstance(
                        risk_profile.get(
                            "settings"
                        ),
                        Mapping,
                    )
                    else None
                )
            )
            if (
                maximum > source_maximum
                or (
                    configured_maximum > ZERO
                    and maximum
                    > configured_maximum
                )
            ):
                errors.append(
                    _issue(
                        "MAXIMUM_WEIGHT_ESCALATED",
                        f"{symbol}不得提高portfolio或Python风险上限",
                        f"{path}.maximum_weight",
                    )
                )
            if (
                portfolio_action == "open"
                and (
                    source.get(
                        "in_current_coarse"
                    )
                    is not True
                    or source.get(
                        "current_position"
                    )
                    is True
                )
            ):
                errors.append(
                    _issue(
                        "NEW_POSITION_NOT_IN_COARSE",
                        f"{symbol}新仓必须来自当前coarse候选",
                        f"{path}.symbol",
                    )
                )
            if (
                portfolio_action
                != source.get("action")
                and not automatic_crypto_liquidation
            ):
                errors.append(
                    _issue(
                        "PORTFOLIO_ACTION_MISMATCH",
                        f"{symbol}的portfolio_action不匹配",
                        f"{path}.portfolio_action",
                    )
                )
        if target < ZERO or maximum < ZERO:
            errors.append(
                _issue(
                    "NEGATIVE_WEIGHT",
                    f"{symbol}权重不得为负",
                    path,
                )
            )
        if target > maximum:
            errors.append(
                _issue(
                    "TARGET_EXCEEDS_MAXIMUM_WEIGHT",
                    f"{symbol}目标权重超过maximum_weight",
                    f"{path}.target_weight",
                )
            )
        difference = abs(
            target - source_target
        )
        relative = (
            difference / abs(source_target)
            if source_target != ZERO
            else (
                ZERO
                if difference == ZERO
                else Decimal("Infinity")
            )
        )
        exceeded = (
            difference > maximum_absolute
            or relative > maximum_relative
        )
        if (
            exceeded
            and not automatic_crypto_liquidation
        ):
            adjustment_breached = True
            if (
                decision != "defer"
                or not requires_replan
            ):
                errors.append(
                    _issue(
                        "WEIGHT_ADJUSTMENT_REQUIRES_REPLAN",
                        f"{symbol}权重调整越界，必须replan并defer",
                        f"{path}.target_weight",
                    )
                )
        if not (
            minimum_fraction
            <= fraction
            <= maximum_fraction
        ):
            errors.append(
                _issue(
                    "EXECUTION_FRACTION_OUT_OF_RANGE",
                    f"{symbol}执行比例超出policy范围",
                    f"{path}.execution_fraction",
                )
            )
        executable = (
            decision in EXECUTABLE_DECISIONS
        )
        price_condition = raw.get(
            "price_condition"
        )
        price_condition = (
            price_condition
            if isinstance(
                price_condition,
                Mapping,
            )
            else {}
        )
        order_intent = raw.get(
            "order_intent"
        )
        order_intent = (
            order_intent
            if isinstance(
                order_intent,
                Mapping,
            )
            else {}
        )
        if decision in NON_EXECUTABLE_DECISIONS:
            if (
                side != "none"
                or fraction != ZERO
                or order_intent.get(
                    "preferred_type"
                )
                != "none"
            ):
                errors.append(
                    _issue(
                        "NON_EXECUTABLE_DECISION_HAS_INTENT",
                        f"{symbol}的{decision}不能形成买卖意图",
                        path,
                    )
                )
        if executable and not permission_enabled:
            errors.append(
                _issue(
                    "TRADE_PERMISSION_DISABLED",
                    f"{symbol}无交易许可不能approve/modify",
                    f"{path}.execution_decision",
                )
            )
        expected_side = (
            "buy"
            if portfolio_action
            in {"open", "increase"}
            else "sell"
            if portfolio_action
            in {"reduce", "close"}
            else "none"
        )
        if executable and side != expected_side:
            errors.append(
                _issue(
                    "SIDE_PORTFOLIO_MISMATCH",
                    f"{symbol}的side与portfolio动作不一致",
                    f"{path}.side",
                )
            )
        if conservative_defer and executable:
            errors.append(
                _issue(
                    "UNRESOLVED_REVIEW_MUST_DEFER",
                    f"{symbol}存在未解析用户限制，必须defer",
                    f"{path}.execution_decision",
                )
            )
        if (
            executable
            and side == "buy"
            and _structured_prohibits(
                prohibitions,
                symbol,
            )
        ):
            errors.append(
                _issue(
                    "USER_PROHIBITION_VIOLATED",
                    f"{symbol}违反用户禁止",
                    f"{path}.execution_decision",
                )
            )
        if (
            requires_replan
            and executable
            and not automatic_crypto_liquidation
        ):
            errors.append(
                _issue(
                    "REPLAN_CANNOT_EXECUTE",
                    f"{symbol}要求replan时不得执行",
                    f"{path}.execution_decision",
                )
            )
        if not executable:
            continue
        is_crypto = (
            asset.get("asset_class") == "crypto"
        )
        is_closed_equity = (
            market_phase in CLOSED_EQUITY_PHASES
            and not is_crypto
        )
        if (
            market_phase == "unknown"
            and not is_crypto
        ):
            errors.append(
                _issue(
                    "UNKNOWN_PHASE_CANNOT_APPROVE",
                    "unknown市场阶段不得approve/modify",
                    f"{path}.execution_decision",
                )
            )
        if is_closed_equity:
            maximum_closed_fraction = (
                closed_open_fraction
                if portfolio_action
                in {"open", "increase"}
                else closed_reduce_fraction
            )
            if (
                profile_environment != "live"
                or closed_policy.get("enabled")
                is not True
                or maximum_closed_fraction
                <= ZERO
                or fraction
                > maximum_closed_fraction
            ):
                errors.append(
                    _issue(
                        "CLOSED_SESSION_FRACTION_INVALID",
                        f"{symbol}闭市排队执行比例超过保守上限",
                        f"{path}.execution_fraction",
                    )
                )
        quote = quotes.get(symbol)
        quote = (
            quote
            if isinstance(quote, Mapping)
            else {}
        )
        quote_age = _decimal_or_zero(
            quote.get("quote_age_seconds")
        )
        if (
            not automatic_crypto_liquidation
            and (
                quote.get("status") != "success"
                or quote.get("quote_age_seconds")
                is None
                or quote_age
                > (
                    closed_quote_age_limit
                    if is_closed_equity
                    else quote_age_limit
                )
            )
        ):
            errors.append(
                _issue(
                    "QUOTE_STALE_OR_MISSING",
                    f"{symbol}报价缺失或过期",
                    path,
                )
            )
        spread_limit = (
            regular_spread_limit
            if (
                market_phase == "regular_session"
                or is_crypto
            )
            else extended_spread_limit
        )
        spread = _decimal_or_zero(
            quote.get("spread_bps")
        )
        if (
            not automatic_crypto_liquidation
            and (
                quote.get("spread_bps") is None
                or spread > spread_limit
            )
        ):
            errors.append(
                _issue(
                    "SPREAD_LIMIT_BREACHED",
                    f"{symbol}价差超过风险上限",
                    path,
                )
            )
        if (
            asset.get("tradable") is not True
            or asset.get("status")
            != "active"
        ):
            errors.append(
                _issue(
                    "ASSET_NOT_TRADABLE",
                    f"{symbol}资产不可交易",
                    path,
                )
            )
        reference = str(
            price_condition.get(
                "reference",
                "none",
            )
        )
        reference_available = {
            "bid": quote.get("bid_price"),
            "ask": quote.get("ask_price"),
            "midpoint": quote.get("midpoint"),
            "last_trade": (
                trades.get(symbol, {}).get(
                    "price"
                )
                if isinstance(
                    trades.get(symbol),
                    Mapping,
                )
                else None
            ),
        }.get(reference)
        if (
            reference_available is None
            and automatic_crypto_liquidation
        ):
            reference_available = position.get(
                "current_price"
            )
        if (
            reference == "none"
            or reference_available is None
        ):
            errors.append(
                _issue(
                    "PRICE_REFERENCE_UNAVAILABLE",
                    f"{symbol}缺少有效价格参考",
                    f"{path}.price_condition.reference",
                )
            )
        preferred_type = order_intent.get(
            "preferred_type"
        )
        if (
            preferred_type == "limit"
            and price_condition.get(
                "limit_price"
            )
            is None
        ):
            errors.append(
                _issue(
                    "LIMIT_INTENT_PRICE_MISSING",
                    f"{symbol}限价意图缺少limit_price",
                    f"{path}.price_condition.limit_price",
                )
            )
        numeric_prices: dict[str, Decimal] = {}
        for price_field in (
            "limit_price",
            "do_not_execute_above",
            "review_below",
        ):
            raw_price = price_condition.get(
                price_field
            )
            if raw_price is None:
                continue
            try:
                parsed_price = _decimal(
                    raw_price
                )
            except (
                InvalidOperation,
                ValueError,
            ):
                parsed_price = ZERO
            if parsed_price <= ZERO:
                errors.append(
                    _issue(
                        "INVALID_PRICE_CONDITION",
                        f"{symbol}价格条件必须是正数",
                        (
                            f"{path}.price_condition."
                            f"{price_field}"
                        ),
                    )
                )
            else:
                numeric_prices[
                    price_field
                ] = parsed_price
        upper = numeric_prices.get(
            "do_not_execute_above"
        )
        lower = numeric_prices.get(
            "review_below"
        )
        limit = numeric_prices.get(
            "limit_price"
        )
        if (
            upper is not None
            and lower is not None
            and lower > upper
        ) or (
            side == "buy"
            and limit is not None
            and upper is not None
            and limit > upper
        ):
            errors.append(
                _issue(
                    "PRICE_CONDITION_INCONSISTENT",
                    f"{symbol}价格条件相互冲突",
                    f"{path}.price_condition",
                )
            )
        if is_crypto:
            if (
                side != "sell"
                or portfolio_action
                not in {"reduce", "close"}
            ):
                errors.append(
                    _issue(
                        "CRYPTO_POSITION_EXPANSION_FORBIDDEN",
                        f"{symbol}加密资产只允许减仓或清仓",
                        path,
                    )
                )
            if (
                preferred_type
                not in {"market", "limit"}
                or order_intent.get(
                    "time_in_force_preference"
                )
                not in {"gtc", "ioc"}
                or order_intent.get(
                    "extended_hours_requested"
                )
                is True
            ):
                errors.append(
                    _issue(
                        "CRYPTO_ORDER_INTENT_INVALID",
                        f"{symbol}加密订单必须使用market/limit、gtc/ioc且不标记extended_hours",
                        path,
                    )
                )
        elif is_closed_equity:
            if (
                preferred_type != "limit"
                or order_intent.get(
                    "time_in_force_preference"
                )
                != closed_policy.get(
                    "time_in_force"
                )
                or order_intent.get(
                    "extended_hours_requested"
                )
                is not False
                or order_intent.get(
                    "allow_queue"
                )
                is not True
                or price_condition.get(
                    "limit_price"
                )
                is None
            ):
                errors.append(
                    _issue(
                        "CLOSED_SESSION_QUEUE_INTENT_INVALID",
                        f"{symbol}闭市排队必须使用带价格的limit/day且allow_queue=true",
                        path,
                    )
                )
        elif market_phase != "regular_session":
            supported_phases = capability.get(
                "supported_phases",
                [],
            )
            supported = (
                capability.get("supported")
                is True
                and isinstance(
                    supported_phases,
                    list,
                )
                and market_phase
                in supported_phases
            )
            if not supported:
                errors.append(
                    _issue(
                        "EXTENDED_HOURS_UNSUPPORTED",
                        f"{market_phase}不受券商扩展时段能力支持",
                        path,
                    )
                )
            if (
                preferred_type != "limit"
                or order_intent.get(
                    "extended_hours_requested"
                )
                is not True
                or price_condition.get(
                    "limit_price"
                )
                is None
            ):
                errors.append(
                    _issue(
                        "EXTENDED_HOURS_INTENT_INVALID",
                        f"{symbol}扩展时段必须使用带价格的limit intent",
                        path,
                    )
                )
    if len(seen) != len(set(seen)):
        errors.append(
            _issue(
                "DUPLICATE_EXECUTION_SYMBOL",
                "execution decisions不得重复symbol",
                "$.decisions",
            )
        )
    errors.extend(
        _protection_issues(
            payload,
            input_payload=input_payload,
            snapshot=snapshot,
            decisions=decisions,
        )
    )
    if adjustment_breached and not requires_replan:
        errors.append(
            _issue(
                "REPLAN_FLAG_MISSING",
                "存在越界调整但未设置requires_portfolio_replan",
                "$.requires_portfolio_replan",
            )
        )

    open_orders = snapshot.get(
        "open_orders",
        [],
    )
    open_symbols = {
        str(item.get("symbol", "")).upper()
        for item in open_orders
        if isinstance(item, Mapping)
    } if isinstance(open_orders, list) else set()
    open_actions = payload.get(
        "open_order_actions",
        [],
    )
    if isinstance(open_actions, list):
        seen_order_actions: set[
            tuple[str, str]
        ] = set()
        for index, action in enumerate(
            open_actions
        ):
            if not isinstance(action, Mapping):
                continue
            symbol = str(
                action.get("symbol", "")
            ).upper()
            order_reference = str(
                action.get(
                    "order_reference",
                    "",
                )
            )
            action_key = (
                order_reference,
                symbol,
            )
            if action_key in seen_order_actions:
                errors.append(
                    _issue(
                        "DUPLICATE_OPEN_ORDER_ACTION",
                        "同一挂单不得给出重复动作",
                        (
                            "$.open_order_actions"
                            f"[{index}]"
                        ),
                    )
                )
            seen_order_actions.add(action_key)
            if symbol not in open_symbols:
                errors.append(
                    _issue(
                        "OPEN_ORDER_ACTION_UNKNOWN",
                        f"{symbol}没有当前未完成订单",
                        (
                            "$.open_order_actions"
                            f"[{index}].symbol"
                        ),
                    )
                )
    for forbidden_path in _forbidden_paths(
        payload
    ):
        errors.append(
            _issue(
                "FORBIDDEN_EXECUTION_FIELD",
                "execution输出含最终数量、订单或成交声明",
                forbidden_path,
            )
        )
    if payload.get("status") == "success_local_only":
        raw_warnings = payload.get(
            "warnings",
            [],
        )
        network = payload.get(
            "network_research",
            {},
        )
        network_warnings = (
            network.get("warnings", [])
            if isinstance(network, Mapping)
            else []
        )
        if not raw_warnings or not network_warnings:
            errors.append(
                _issue(
                    "LOCAL_ONLY_WARNING_MISSING",
                    "本地降级成功必须明确记录网络限制",
                    "$.warnings",
                )
            )
        warnings.append(
            _issue(
                "EXECUTION_LOCAL_ONLY",
                "执行判断仅使用本地资料，订单阶段必须重新核验",
                "$.status",
            )
        )
    schema_valid = not schema_errors
    business_valid = not errors
    return ExecutionValidationResult(
        valid=schema_valid and business_valid,
        schema_valid=schema_valid,
        business_valid=business_valid,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
