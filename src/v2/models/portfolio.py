"""构建并校验 WA Trader v2 Stage D 的战略组合决策。

作用：把粗选、账户、持仓、挂单、风险配置与用户建议合成为稳定输入，
并用 Decimal、严格 Schema 和业务规则验证目标权重。
重要性：这是组合研究与未来订单执行之间的安全边界；本模块只表达权重，
绝不生成最终数量、名义金额或订单。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from jsonschema import Draft202012Validator

from v2.profiles import RiskProfile
from v2.releases import StrategyRelease, sha256_file
from v2.runtime import CyclePaths, utc_now_iso


PORTFOLIO_INPUT_SCHEMA_VERSION = "1.0"
PORTFOLIO_FORBIDDEN_OUTPUT_FIELDS = {
    "quantity",
    "qty",
    "notional",
    "order_type",
    "time_in_force",
    "extended_hours",
    "client_order_id",
    "broker_order_id",
    "submit",
    "submitted",
    "filled",
}
ZERO = Decimal("0")
ONE = Decimal("1")


def _canonical_hash(payload: object) -> str:
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
    return Decimal(str(value))


def _decimal_or_zero(value: object) -> Decimal:
    try:
        return _decimal(value)
    except (InvalidOperation, ValueError):
        return ZERO


def _decimal_text(value: object) -> str | None:
    try:
        number = _decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return format(number, "f")


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


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
class PortfolioCandidate:
    symbol: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioHolding:
    symbol: str
    current_weight: Decimal
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioOpenOrder:
    symbol: str
    remaining_quantity: Decimal
    reserved_capital_estimate: Decimal
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioMarketContext:
    market_phase: str
    market_data_cutoff: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioInput:
    payload: Mapping[str, Any]
    input_signature: str


@dataclass(frozen=True)
class PortfolioPricePlan:
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioProtectionPlan:
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioDecision:
    symbol: str
    action: str
    target_weight: Decimal
    maximum_weight: Decimal
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioAllocation:
    target_cash_weight: Decimal
    target_invested_weight: Decimal
    target_position_count: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioOutput:
    allocation: PortfolioAllocation
    decisions: tuple[PortfolioDecision, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PortfolioValidationResult:
    valid: bool
    schema_valid: bool
    business_valid: bool
    errors: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "stage": "portfolio_decision",
            "valid": self.valid,
            "schema_valid": self.schema_valid,
            "business_valid": self.business_valid,
            "errors": [dict(item) for item in self.errors],
            "warnings": [
                dict(item) for item in self.warnings
            ],
        }


@dataclass(frozen=True)
class PortfolioReuseDecision:
    action: Literal["run", "reuse", "block"]
    source_cycle_id: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioInputBuildResult:
    payload: dict[str, Any]
    input_signature: str
    positions_fingerprint: str
    open_orders_fingerprint: str
    capital_fingerprint: str
    market_data_cutoff: str


def build_positions_fingerprint(
    positions: list[Mapping[str, Any]],
) -> str:
    stable = [
        {
            "symbol": str(item.get("symbol", "")).upper(),
            "side": str(item.get("side", "")),
            "quantity": _decimal_text(item.get("quantity")),
            "available_quantity": _decimal_text(
                item.get("available_quantity")
            ),
            "average_entry_price": _decimal_text(
                item.get("average_entry_price")
            ),
        }
        for item in positions
    ]
    stable.sort(key=lambda item: item["symbol"])
    return _canonical_hash(stable)


def build_open_orders_fingerprint(
    orders: list[Mapping[str, Any]],
) -> str:
    stable: list[dict[str, Any]] = []
    for item in orders:
        quantity = _decimal_or_zero(
            item.get("quantity")
        )
        filled = _decimal_or_zero(
            item.get("filled_quantity")
        )
        stable.append(
            {
                "client_order_id": str(
                    item.get("client_order_id", "")
                ),
                "symbol": str(
                    item.get("symbol", "")
                ).upper(),
                "side": str(item.get("side", "")),
                "type": str(item.get("type", "")),
                "remaining_quantity": format(
                    max(ZERO, quantity - filled),
                    "f",
                ),
                "limit_price": _decimal_text(
                    item.get("limit_price")
                ),
                "stop_price": _decimal_text(
                    item.get("stop_price")
                ),
                "status": str(item.get("status", "")),
                "extended_hours": bool(
                    item.get("extended_hours", False)
                ),
            }
        )
    stable.sort(
        key=lambda item: (
            item["client_order_id"],
            item["symbol"],
        )
    )
    return _canonical_hash(stable)


def build_capital_fingerprint(
    capital: Mapping[str, Any],
) -> str:
    return _canonical_hash(
        {
            "allocatable_capital_estimate": (
                _decimal_text(
                    capital.get(
                        "allocatable_capital_estimate"
                    )
                )
            )
        }
    )


def _normalize_money_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = payload.get(field)
        normalized = _decimal_text(value)
        result[field] = (
            normalized
            if normalized is not None
            else value
        )
    return result


def _portfolio_positions(
    positions: list[Mapping[str, Any]],
    *,
    portfolio_value: Decimal,
    coarse_symbols: set[str],
    eligible_symbols: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in positions:
        symbol = str(
            source.get("symbol", "")
        ).upper()
        market_value = _decimal_or_zero(
            source.get("market_value")
        )
        current_weight = (
            market_value / portfolio_value
            if portfolio_value > ZERO
            else ZERO
        )
        result.append(
            {
                "symbol": symbol,
                "side": str(
                    source.get("side", "long")
                ),
                "quantity": _decimal_text(
                    source.get("quantity")
                ),
                "available_quantity": _decimal_text(
                    source.get("available_quantity")
                ),
                "average_entry_price": _decimal_text(
                    source.get("average_entry_price")
                ),
                "current_price": _decimal_text(
                    source.get("current_price")
                ),
                "market_value": _decimal_text(
                    source.get("market_value")
                ),
                "current_weight": format(
                    current_weight,
                    "f",
                ),
                "in_current_coarse": (
                    symbol in coarse_symbols
                ),
                "new_position_screen_eligible": (
                    symbol in eligible_symbols
                ),
            }
        )
    return result


def _portfolio_orders(
    orders: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in orders:
        quantity = _decimal_or_zero(
            source.get("quantity")
        )
        filled = _decimal_or_zero(
            source.get("filled_quantity")
        )
        remaining = max(ZERO, quantity - filled)
        price = _decimal_or_zero(
            source.get("limit_price")
            if source.get("limit_price") is not None
            else source.get("stop_price")
        )
        reserved = (
            remaining * price
            if str(source.get("side", "")).lower()
            == "buy"
            else ZERO
        )
        result.append(
            {
                "client_order_id": str(
                    source.get("client_order_id", "")
                ),
                "symbol": str(
                    source.get("symbol", "")
                ).upper(),
                "side": str(source.get("side", "")),
                "type": str(source.get("type", "")),
                "quantity": format(quantity, "f"),
                "filled_quantity": format(
                    filled,
                    "f",
                ),
                "remaining_quantity": format(
                    remaining,
                    "f",
                ),
                "limit_price": _decimal_text(
                    source.get("limit_price")
                ),
                "stop_price": _decimal_text(
                    source.get("stop_price")
                ),
                "status": str(
                    source.get("status", "")
                ),
                "extended_hours": bool(
                    source.get("extended_hours", False)
                ),
                "reserved_capital_estimate": format(
                    reserved,
                    "f",
                ),
            }
        )
    return result


def build_portfolio_input(
    *,
    paths: CyclePaths,
    base_snapshot: Mapping[str, Any],
    coarse_output: Mapping[str, Any],
    coarse_input: Mapping[str, Any],
    initial_guidance: Mapping[str, Any],
    policy: Mapping[str, Any],
    risk_profile: RiskProfile,
    release: StrategyRelease,
    trigger: Mapping[str, Any] | None = None,
    previous_portfolio: Mapping[str, Any] | None = None,
) -> PortfolioInputBuildResult:
    """Create one deterministic Stage D input; cycle-local fields are unsigned."""

    account_source = base_snapshot.get("account")
    if not isinstance(account_source, Mapping):
        account_source = {}
    capital_source = base_snapshot.get("capital")
    if not isinstance(capital_source, Mapping):
        capital_source = {}
    raw_positions = base_snapshot.get("positions")
    positions_source = [
        item
        for item in raw_positions
        if isinstance(item, Mapping)
    ] if isinstance(raw_positions, list) else []
    raw_orders = base_snapshot.get("open_orders")
    orders_source = [
        item
        for item in raw_orders
        if isinstance(item, Mapping)
    ] if isinstance(raw_orders, list) else []
    raw_selections = coarse_output.get("selections")
    selections = [
        item
        for item in raw_selections
        if isinstance(item, Mapping)
    ] if isinstance(raw_selections, list) else []
    coarse_symbols = {
        str(item.get("symbol", "")).upper()
        for item in selections
    }
    eligible_symbols = {
        str(item.get("symbol", "")).upper()
        for item in selections
        if item.get(
            "screen_new_position_eligible"
        ) is True
    }
    universe = coarse_input.get("universe")
    universe_items = (
        universe
        if isinstance(universe, list)
        else universe.get("items", [])
        if isinstance(universe, Mapping)
        else []
    )
    universe_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in universe_items
        if isinstance(item, Mapping)
    }
    candidates: list[dict[str, Any]] = []
    for selection in selections:
        symbol = str(
            selection.get("symbol", "")
        ).upper()
        universe_item = universe_by_symbol.get(
            symbol,
            {},
        )
        candidates.append(
            {
                **dict(selection),
                "symbol": symbol,
                "daily_summary": dict(
                    universe_item.get(
                        "daily_summary",
                        {},
                    )
                )
                if isinstance(
                    universe_item.get("daily_summary"),
                    Mapping,
                )
                else {},
                "intraday_summary": {
                    "status": "no_data"
                },
                "latest_quote": {
                    "status": "no_data"
                },
                "asset_status": dict(
                    universe_item.get(
                        "asset_status",
                        {},
                    )
                )
                if isinstance(
                    universe_item.get("asset_status"),
                    Mapping,
                )
                else {},
            }
        )

    account = {
        "status": str(
            account_source.get("status", "")
        ),
        "trading_blocked": bool(
            account_source.get(
                "trading_blocked",
                False,
            )
            or account_source.get(
                "account_blocked",
                False,
            )
            or account_source.get(
                "trade_suspended_by_user",
                False,
            )
        ),
        **_normalize_money_fields(
            account_source,
            (
                "cash",
                "buying_power",
                "portfolio_value",
                "equity",
            ),
        ),
    }
    capital = _normalize_money_fields(
        capital_source,
        (
            "cash",
            "buying_power",
            "open_order_reserved_estimate",
            "allocatable_capital_estimate",
        ),
    )
    portfolio_value = _decimal_or_zero(
        account.get("portfolio_value")
    )
    positions = _portfolio_positions(
        positions_source,
        portfolio_value=portfolio_value,
        coarse_symbols=coarse_symbols,
        eligible_symbols=eligible_symbols,
    )
    orders = _portfolio_orders(orders_source)
    positions_fingerprint = (
        build_positions_fingerprint(
            positions_source
        )
    )
    open_orders_fingerprint = (
        build_open_orders_fingerprint(
            orders_source
        )
    )
    capital_fingerprint = (
        build_capital_fingerprint(
            capital_source
        )
    )
    coarse_market = coarse_input.get(
        "market_context",
        {},
    )
    coarse_market = (
        coarse_market
        if isinstance(coarse_market, Mapping)
        else {}
    )
    market_data_cutoff = str(
        coarse_market.get("latest_daily_date")
        or coarse_output.get("generated_at")
        or base_snapshot.get("retrieved_at")
        or utc_now_iso()
    )
    sector_exposure: dict[str, Decimal] = {}
    candidate_sectors = {
        str(item.get("symbol", "")).upper(): str(
            item.get("sector") or "Unknown"
        )
        for item in selections
    }
    for holding in positions:
        sector = candidate_sectors.get(
            holding["symbol"],
            "Unknown",
        )
        sector_key = (
            sector
            if sector != "Unknown"
            else f"Unknown:{holding['symbol']}"
        )
        sector_exposure[sector_key] = (
            sector_exposure.get(sector_key, ZERO)
            + _decimal_or_zero(
                holding.get("current_weight")
            )
        )
    current_invested = sum(
        (
            _decimal_or_zero(
                item.get("current_weight")
            )
            for item in positions
        ),
        ZERO,
    )
    market_context = {
        "market_phase": str(
            base_snapshot.get(
                "market_phase",
                "unknown",
            )
        ),
        "broad_market": dict(
            coarse_input.get(
                "market_context",
                {},
            )
        )
        if isinstance(
            coarse_input.get("market_context"),
            Mapping,
        )
        else {},
        "sector_etfs": {},
        "risk_proxies": {},
        "current_cash_weight": format(
            max(ZERO, ONE - current_invested),
            "f",
        ),
        "current_sector_exposure": {
            key: format(value, "f")
            for key, value
            in sorted(sector_exposure.items())
        },
        "data_quality": dict(
            base_snapshot.get(
                "data_quality",
                {},
            )
        )
        if isinstance(
            base_snapshot.get("data_quality"),
            Mapping,
        )
        else {},
        "market_data_cutoff": market_data_cutoff,
    }
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
    coarse_payload = {
        "input_signature": str(
            coarse_output.get(
                "input_signature",
                "",
            )
        ),
        "output_hash": _canonical_hash(
            coarse_output
        ),
        "selection_count": len(selections),
        "output": dict(coarse_output),
    }
    portfolio_artifacts = {
        "prompt_hash": release.prompt_hashes.get(
            "prompts/portfolio.md",
            "",
        ),
        "agents_hash": release.prompt_hashes.get(
            "prompts/portfolio_AGENTS.md",
            "",
        ),
        "schema_hash": release.schema_hashes.get(
            "schemas/portfolio_output.schema.json",
            "",
        ),
        "policy_hash": release.config_hashes.get(
            "config/portfolio_policy.json",
            "",
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
        "coarse_input_signature": (
            coarse_payload["input_signature"]
        ),
        "coarse_output_hash": (
            coarse_payload["output_hash"]
        ),
        "positions_fingerprint": (
            positions_fingerprint
        ),
        "open_orders_fingerprint": (
            open_orders_fingerprint
        ),
        "allocatable_capital": capital.get(
            "allocatable_capital_estimate"
        ),
        "market_data_cutoff": market_data_cutoff,
        "portfolio_artifacts": (
            portfolio_artifacts
        ),
    }
    input_signature = _canonical_hash(
        signature_payload
    )
    generated_at = utc_now_iso()
    payload = {
        "schema_version": (
            PORTFOLIO_INPUT_SCHEMA_VERSION
        ),
        "stage": "portfolio_decision",
        "profile": {
            "profile_id": paths.profile_id
        },
        "release": release_payload,
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "generated_at": generated_at,
        "input_signature": input_signature,
        "input_components": dict(
            signature_payload
        ),
        "trigger": dict(trigger or {}),
        "initial_guidance": dict(
            initial_guidance
        ),
        "coarse": coarse_payload,
        "account": account,
        "capital": capital,
        "positions": positions,
        "open_orders": orders,
        "candidates": candidates,
        "market_context": market_context,
        "previous_portfolio": dict(
            previous_portfolio or {}
        ),
        "data_quality": dict(
            base_snapshot.get(
                "data_quality",
                {},
            )
        )
        if isinstance(
            base_snapshot.get("data_quality"),
            Mapping,
        )
        else {},
        "policy": {
            **dict(policy),
            "risk_profile": dict(
                risk_profile.settings
            ),
        },
    }
    return PortfolioInputBuildResult(
        payload=payload,
        input_signature=input_signature,
        positions_fingerprint=positions_fingerprint,
        open_orders_fingerprint=(
            open_orders_fingerprint
        ),
        capital_fingerprint=capital_fingerprint,
        market_data_cutoff=market_data_cutoff,
    )


def _forbidden_paths(
    node: object,
    path: str = "$",
) -> list[str]:
    result: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in PORTFOLIO_FORBIDDEN_OUTPUT_FIELDS:
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


def validate_portfolio_output(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    now: datetime | None = None,
) -> PortfolioValidationResult:
    """Validate schema, identity, weights, scope, capital and forbidden keys."""

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

    expected_identity = {
        "stage": "portfolio_decision",
        "profile_id": input_payload.get(
            "profile",
            {},
        ).get("profile_id")
        if isinstance(
            input_payload.get("profile"),
            Mapping,
        )
        else None,
        "strategy_id": input_payload.get(
            "release",
            {},
        ).get("strategy_id")
        if isinstance(
            input_payload.get("release"),
            Mapping,
        )
        else None,
        "strategy_version": input_payload.get(
            "release",
            {},
        ).get("strategy_version")
        if isinstance(
            input_payload.get("release"),
            Mapping,
        )
        else None,
        "run_date": input_payload.get("run_date"),
        "cycle_id": input_payload.get("cycle_id"),
        "input_signature": input_payload.get(
            "input_signature"
        ),
    }
    for field, expected in expected_identity.items():
        if payload.get(field) != expected:
            errors.append(
                _issue(
                    f"{field.upper()}_MISMATCH",
                    f"{field}与portfolio输入不一致",
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
                "portfolio status无效",
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
        current = current.astimezone(timezone.utc)
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
    elif current is not None and valid_until <= current:
        errors.append(
            _issue(
                "PORTFOLIO_EXPIRED",
                "portfolio方案已过期",
                "$.valid_until",
            )
        )

    allocation = payload.get("allocation")
    allocation = (
        allocation
        if isinstance(allocation, Mapping)
        else {}
    )
    try:
        cash_weight = _decimal(
            allocation.get("target_cash_weight")
        )
        invested_weight = _decimal(
            allocation.get(
                "target_invested_weight"
            )
        )
    except (InvalidOperation, ValueError):
        cash_weight = ZERO
        invested_weight = ZERO
        errors.append(
            _issue(
                "ALLOCATION_DECIMAL_INVALID",
                "目标现金与投资权重必须是Decimal字符串",
                "$.allocation",
            )
        )
    if cash_weight + invested_weight != ONE:
        errors.append(
            _issue(
                "ALLOCATION_SUM_MISMATCH",
                "target_cash_weight与target_invested_weight之和必须为1",
                "$.allocation",
            )
        )
    if cash_weight < ZERO or invested_weight < ZERO:
        errors.append(
            _issue(
                "NEGATIVE_ALLOCATION_WEIGHT",
                "目标配置权重不得为负",
                "$.allocation",
            )
        )

    policy = input_payload.get("policy")
    policy = policy if isinstance(
        policy,
        Mapping,
    ) else {}
    risk = policy.get("risk_profile")
    risk = risk if isinstance(
        risk,
        Mapping,
    ) else {}
    maximum_single = _decimal_or_zero(
        risk.get(
            "maximum_single_position_weight",
            allocation.get(
                "maximum_single_symbol_weight"
            ),
        )
    )
    maximum_sector = _decimal_or_zero(
        policy.get(
            "maximum_sector_weight",
            allocation.get("maximum_sector_weight"),
        )
    )
    if maximum_sector <= ZERO:
        maximum_sector = _decimal_or_zero(
            allocation.get(
                "maximum_sector_weight"
            )
        )
    minimum_cash = _decimal_or_zero(
        risk.get("minimum_cash_weight")
    )
    maximum_gross = _decimal_or_zero(
        risk.get("maximum_gross_exposure")
    )
    if cash_weight < minimum_cash:
        errors.append(
            _issue(
                "MINIMUM_CASH_BREACHED",
                "目标现金比例低于风险下限",
                "$.allocation.target_cash_weight",
            )
        )
    if (
        maximum_gross > ZERO
        and invested_weight > maximum_gross
    ):
        errors.append(
            _issue(
                "MAXIMUM_GROSS_EXPOSURE_BREACHED",
                "目标投资比例超过风险总敞口上限",
                "$.allocation.target_invested_weight",
            )
        )
    if (
        _decimal_or_zero(
            allocation.get(
                "maximum_single_symbol_weight"
            )
        )
        > maximum_single
    ):
        errors.append(
            _issue(
                "DECLARED_SINGLE_LIMIT_TOO_HIGH",
                "输出声明的单标的上限超过风险配置",
                "$.allocation.maximum_single_symbol_weight",
            )
        )
    if (
        maximum_sector > ZERO
        and _decimal_or_zero(
            allocation.get(
                "maximum_sector_weight"
            )
        )
        > maximum_sector
    ):
        errors.append(
            _issue(
                "DECLARED_SECTOR_LIMIT_TOO_HIGH",
                "输出声明的行业上限超过策略配置",
                "$.allocation.maximum_sector_weight",
            )
        )

    candidates = input_payload.get("candidates")
    candidates = (
        candidates
        if isinstance(candidates, list)
        else []
    )
    candidate_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in candidates
        if isinstance(item, Mapping)
    }
    positions = input_payload.get("positions")
    positions = (
        positions
        if isinstance(positions, list)
        else []
    )
    holding_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in positions
        if isinstance(item, Mapping)
    }
    decisions = payload.get("decisions")
    decisions = (
        decisions
        if isinstance(decisions, list)
        else []
    )
    symbols: list[str] = []
    positive_total = ZERO
    sector_totals: dict[str, Decimal] = {}
    positive_count = 0
    minimum_target = _decimal_or_zero(
        policy.get(
            "minimum_target_weight",
            "0",
        )
    )
    for index, raw_decision in enumerate(decisions):
        if not isinstance(raw_decision, Mapping):
            continue
        decision_path = f"$.decisions[{index}]"
        symbol = str(
            raw_decision.get("symbol", "")
        ).upper()
        symbols.append(symbol)
        action = str(
            raw_decision.get("action", "")
        )
        target = _decimal_or_zero(
            raw_decision.get("target_weight")
        )
        maximum = _decimal_or_zero(
            raw_decision.get("maximum_weight")
        )
        if target < ZERO or maximum < ZERO:
            errors.append(
                _issue(
                    "NEGATIVE_DECISION_WEIGHT",
                    f"{symbol}权重不得为负",
                    decision_path,
                )
            )
        if target > maximum:
            errors.append(
                _issue(
                    "TARGET_EXCEEDS_MAXIMUM_WEIGHT",
                    f"{symbol}目标权重超过maximum_weight",
                    f"{decision_path}.target_weight",
                )
            )
        if maximum_single > ZERO and target > maximum_single:
            errors.append(
                _issue(
                    "SINGLE_SYMBOL_LIMIT_BREACHED",
                    f"{symbol}目标权重超过风险上限",
                    f"{decision_path}.target_weight",
                )
            )
        if target > ZERO:
            positive_count += 1
            positive_total += target
            if target < minimum_target:
                errors.append(
                    _issue(
                        "TARGET_WEIGHT_BELOW_MINIMUM",
                        f"{symbol}正目标权重低于策略下限",
                        f"{decision_path}.target_weight",
                    )
                )
            candidate = candidate_by_symbol.get(
                symbol,
                {},
            )
            sector = str(
                candidate.get("sector")
                or "Unknown"
            )
            sector_key = (
                sector
                if sector != "Unknown"
                else f"Unknown:{symbol}"
            )
            sector_totals[sector_key] = (
                sector_totals.get(sector_key, ZERO)
                + target
            )
            if sector == "Unknown":
                warnings.append(
                    _issue(
                        "SECTOR_UNKNOWN",
                        f"{symbol}缺少行业分类，按标的单独计算上限",
                        f"{decision_path}.target_weight",
                    )
                )
        holding = holding_by_symbol.get(symbol)
        candidate = candidate_by_symbol.get(symbol)
        if raw_decision.get(
            "current_position"
        ) is not (holding is not None):
            errors.append(
                _issue(
                    "CURRENT_POSITION_FLAG_MISMATCH",
                    f"{symbol}的current_position与输入不一致",
                    f"{decision_path}.current_position",
                )
            )
        if raw_decision.get(
            "in_current_coarse"
        ) is not (candidate is not None):
            errors.append(
                _issue(
                    "CURRENT_COARSE_FLAG_MISMATCH",
                    f"{symbol}的in_current_coarse与输入不一致",
                    f"{decision_path}.in_current_coarse",
                )
            )
        if action == "open":
            if holding is not None:
                errors.append(
                    _issue(
                        "OPEN_HAS_CURRENT_POSITION",
                        f"{symbol}已有持仓不能标记open",
                        f"{decision_path}.action",
                    )
                )
            if (
                candidate is None
                or candidate.get(
                    "screen_new_position_eligible"
                )
                is not True
            ):
                errors.append(
                    _issue(
                        "NEW_POSITION_NOT_ELIGIBLE",
                        f"{symbol}不是可新开仓粗选候选",
                        f"{decision_path}.symbol",
                    )
                )
        if action == "increase" and holding is None:
            errors.append(
                _issue(
                    "INCREASE_WITHOUT_POSITION",
                    f"{symbol}无现有持仓不能increase",
                    f"{decision_path}.action",
                )
            )
        if (
            candidate is None
            and action in {"open", "increase"}
        ):
            errors.append(
                _issue(
                    "OUTSIDE_COARSE_ACTION_FORBIDDEN",
                    f"{symbol}不在coarse中不能{action}",
                    f"{decision_path}.action",
                )
            )
        if action in {"close", "watch", "avoid"} and target != ZERO:
            errors.append(
                _issue(
                    "ZERO_WEIGHT_ACTION_MISMATCH",
                    f"{action}动作的目标权重必须为0",
                    f"{decision_path}.target_weight",
                )
            )
        if action == "reduce" and holding is not None:
            current_weight = _decimal_or_zero(
                holding.get("current_weight")
            )
            if target >= current_weight:
                errors.append(
                    _issue(
                        "REDUCE_WEIGHT_NOT_LOWER",
                        f"{symbol}reduce目标必须低于当前权重",
                        f"{decision_path}.target_weight",
                    )
                )
    if len(symbols) != len(set(symbols)):
        errors.append(
            _issue(
                "DUPLICATE_DECISION_SYMBOL",
                "decisions中的symbol不得重复",
                "$.decisions",
            )
        )
    if positive_total != invested_weight:
        errors.append(
            _issue(
                "INVESTED_WEIGHT_MISMATCH",
                "正目标权重合计必须等于target_invested_weight",
                "$.decisions",
            )
        )
    declared_count = allocation.get(
        "target_position_count"
    )
    if declared_count != positive_count:
        errors.append(
            _issue(
                "TARGET_POSITION_COUNT_MISMATCH",
                "target_position_count与正权重决策数不一致",
                "$.allocation.target_position_count",
            )
        )
    target_holdings = policy.get(
        "target_holdings"
    )
    target_holdings = (
        target_holdings
        if isinstance(target_holdings, Mapping)
        else {}
    )
    minimum_count = int(
        target_holdings.get("minimum", 0)
    )
    maximum_count = int(
        target_holdings.get("maximum", 10**9)
    )
    allow_empty = bool(
        policy.get("allow_empty_portfolio", False)
    )
    if positive_count == 0 and not allow_empty:
        errors.append(
            _issue(
                "EMPTY_PORTFOLIO_FORBIDDEN",
                "策略不允许空组合",
                "$.decisions",
            )
        )
    if positive_count > 0 and not (
        minimum_count
        <= positive_count
        <= maximum_count
    ):
        errors.append(
            _issue(
                "TARGET_HOLDING_COUNT_OUT_OF_RANGE",
                "目标持仓数超出策略范围",
                "$.allocation.target_position_count",
            )
        )
    if maximum_sector > ZERO:
        for sector, weight in sector_totals.items():
            if weight > maximum_sector:
                errors.append(
                    _issue(
                        "SECTOR_LIMIT_BREACHED",
                        f"{sector}目标权重超过行业上限",
                        "$.decisions",
                    )
                )

    account = input_payload.get("account")
    account = account if isinstance(
        account,
        Mapping,
    ) else {}
    capital = input_payload.get("capital")
    capital = capital if isinstance(
        capital,
        Mapping,
    ) else {}
    portfolio_value = _decimal_or_zero(
        account.get("portfolio_value")
    )
    current_value = sum(
        (
            _decimal_or_zero(
                item.get("market_value")
            )
            for item in positions
            if isinstance(item, Mapping)
        ),
        ZERO,
    )
    allocatable = _decimal_or_zero(
        capital.get(
            "allocatable_capital_estimate"
        )
    )
    target_value = (
        invested_weight * portfolio_value
    )
    tolerance = _decimal_or_zero(
        policy.get("weight_tolerance", "0")
    ) * portfolio_value
    if target_value > (
        current_value + allocatable + tolerance
    ):
        errors.append(
            _issue(
                "TARGET_CAPITAL_EXCEEDS_AVAILABLE",
                "目标投资资本超过持仓价值加可分配资金",
                "$.allocation.target_invested_weight",
            )
        )
    for forbidden_path in _forbidden_paths(
        payload
    ):
        errors.append(
            _issue(
                "FORBIDDEN_OUTPUT_FIELD",
                "portfolio输出含有订单或最终数量字段",
                forbidden_path,
            )
        )
    guidance_response = payload.get(
        "guidance_response"
    )
    if not isinstance(guidance_response, Mapping):
        errors.append(
            _issue(
                "GUIDANCE_RESPONSE_MISSING",
                "portfolio必须说明如何处理initial guidance",
                "$.guidance_response",
            )
        )
    if payload.get("status") == "success_local_only":
        warnings.append(
            _issue(
                "PORTFOLIO_LOCAL_ONLY",
                "组合仅使用本地资料，第三阶段必须重新核验",
                "$.status",
            )
        )
    schema_valid = not schema_errors
    business_valid = not errors
    return PortfolioValidationResult(
        valid=schema_valid and business_valid,
        schema_valid=schema_valid,
        business_valid=business_valid,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def should_run_portfolio(
    context: Mapping[str, Any],
) -> PortfolioReuseDecision:
    """Choose run/reuse/block from explicit, already-validated facts."""

    blocking = tuple(
        str(value)
        for value in context.get(
            "blocking_reasons",
            (),
        )
    )
    if blocking:
        return PortfolioReuseDecision(
            action="block",
            source_cycle_id=None,
            reasons=blocking,
        )
    if context.get("force_rebalance") is True:
        return PortfolioReuseDecision(
            action="run",
            source_cycle_id=None,
            reasons=("force_rebalance",),
        )
    source_cycle_id = context.get(
        "source_cycle_id"
    )
    if not source_cycle_id:
        return PortfolioReuseDecision(
            action="run",
            source_cycle_id=None,
            reasons=("no_same_day_valid_portfolio",),
        )
    if (
        context.get("input_signature")
        != context.get("source_input_signature")
    ):
        return PortfolioReuseDecision(
            action="run",
            source_cycle_id=None,
            reasons=("portfolio_input_changed",),
        )
    if context.get("source_valid") is not True:
        return PortfolioReuseDecision(
            action="run",
            source_cycle_id=None,
            reasons=("source_portfolio_invalid",),
        )
    valid_until = _parse_datetime(
        context.get("source_valid_until")
    )
    now = _parse_datetime(
        context.get("now")
    ) or datetime.now(timezone.utc)
    if valid_until is None or valid_until <= now:
        return PortfolioReuseDecision(
            action="run",
            source_cycle_id=None,
            reasons=("source_portfolio_expired",),
        )
    return PortfolioReuseDecision(
        action="reuse",
        source_cycle_id=str(source_cycle_id),
        reasons=(),
    )
