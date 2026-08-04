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
from datetime import date, datetime, timedelta, timezone
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


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


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


def _valuation_price_reference(
    *,
    symbol: str,
    daily_summary: Mapping[str, Any],
    positions_by_symbol: Mapping[str, Mapping[str, Any]],
    snapshot_retrieved_at: object,
) -> dict[str, Any]:
    """Expose an auditable strategic price reference, never a live quote."""

    position = positions_by_symbol.get(symbol, {})
    position_price = _decimal_text(
        position.get("current_price")
    )
    if (
        position_price is not None
        and _decimal_or_zero(position_price) > ZERO
    ):
        return {
            "status": "position_snapshot",
            "reference_price": position_price,
            "observed_at": (
                str(snapshot_retrieved_at)
                if snapshot_retrieved_at
                else None
            ),
            "is_live_quote": False,
            "execution_revalidation_required": True,
        }

    daily_price = _decimal_text(
        daily_summary.get("last_close")
    )
    if (
        daily_price is not None
        and _decimal_or_zero(daily_price) > ZERO
    ):
        return {
            "status": "daily_close",
            "reference_price": daily_price,
            "observed_at": daily_summary.get(
                "last_bar_date"
            ),
            "is_live_quote": False,
            "execution_revalidation_required": True,
        }

    return {
        "status": "no_data",
        "reference_price": None,
        "observed_at": None,
        "is_live_quote": False,
        "execution_revalidation_required": True,
    }


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
    positions_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in positions_source
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
        daily_summary_source = universe_item.get(
            "daily_summary",
            {},
        )
        daily_summary = (
            dict(daily_summary_source)
            if isinstance(
                daily_summary_source,
                Mapping,
            )
            else {}
        )
        candidates.append(
            {
                **dict(selection),
                "symbol": symbol,
                "source": str(
                    universe_item.get(
                        "source",
                        selection.get(
                            "source",
                            "",
                        ),
                    )
                ),
                "daily_summary": daily_summary,
                "intraday_summary": {
                    "status": "no_data"
                },
                "latest_quote": (
                    _valuation_price_reference(
                        symbol=symbol,
                        daily_summary=daily_summary,
                        positions_by_symbol=(
                            positions_by_symbol
                        ),
                        snapshot_retrieved_at=(
                            base_snapshot.get(
                                "retrieved_at"
                            )
                        ),
                    )
                ),
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
        sector_key = sector
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
    current = now or datetime.now(timezone.utc)
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
    elif generated_at > current:
        errors.append(
            _issue(
                "PORTFOLIO_GENERATED_IN_FUTURE",
                "portfolio generated_at不能晚于验证时间",
                "$.generated_at",
            )
        )
    elif valid_until <= current:
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

    run_day = _parse_date(input_payload.get("run_date"))
    cash_policy = policy.get("cash_management", {})
    cash_policy = (
        cash_policy
        if isinstance(cash_policy, Mapping)
        else {}
    )
    high_cash_threshold = _decimal_or_zero(
        cash_policy.get("high_cash_weight_threshold", "1")
    )
    if cash_policy and cash_weight >= high_cash_threshold:
        cash_management = payload.get("cash_management", {})
        cash_management = (
            cash_management
            if isinstance(cash_management, Mapping)
            else {}
        )
        deployment_triggers = cash_management.get(
            "deployment_triggers", []
        )
        if not isinstance(deployment_triggers, list):
            deployment_triggers = []
        minimum_triggers = int(
            cash_policy.get("minimum_deployment_triggers", 0)
        )
        if len(deployment_triggers) < minimum_triggers:
            errors.append(
                _issue(
                    "HIGH_CASH_DEPLOYMENT_TRIGGERS_INSUFFICIENT",
                    "高现金配置必须给出足够的可执行部署触发器",
                    "$.cash_management.deployment_triggers",
                )
            )
        review_day = _parse_date(
            cash_management.get("review_by")
        )
        maximum_review_days = int(
            cash_policy.get("maximum_review_days", 0)
        )
        if (
            run_day is None
            or review_day is None
            or review_day < run_day
            or review_day
            > run_day + timedelta(days=maximum_review_days)
        ):
            errors.append(
                _issue(
                    "HIGH_CASH_REVIEW_DATE_INVALID",
                    "高现金配置必须在策略期限内复核",
                    "$.cash_management.review_by",
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
    competition_requirements = policy.get(
        "capital_competition_requirements",
        {},
    )
    competition_requirements = (
        competition_requirements
        if isinstance(
            competition_requirements,
            Mapping,
        )
        else {}
    )
    if (
        competition_requirements.get(
            "enabled"
        )
        is True
    ):
        competition = payload.get(
            "capital_competition",
            {},
        )
        competition = (
            competition
            if isinstance(competition, Mapping)
            else {}
        )
        ranked_uses = competition.get(
            "ranked_uses",
            [],
        )
        ranked_uses = (
            ranked_uses
            if isinstance(ranked_uses, list)
            else []
        )
        ranks = [
            item.get("rank")
            for item in ranked_uses
            if isinstance(item, Mapping)
        ]
        if ranks and (
            len(ranks) != len(set(ranks))
            or sorted(ranks)
            != list(range(1, len(ranks) + 1))
        ):
            errors.append(
                _issue(
                    "CAPITAL_COMPETITION_RANKS_INVALID",
                    "资本竞争排名必须连续且无重复",
                    "$.capital_competition.ranked_uses",
                )
            )
        cash_count = sum(
            isinstance(item, Mapping)
            and item.get("comparator_type")
            == "cash"
            for item in ranked_uses
        )
        if (
            competition_requirements.get(
                "require_cash_comparator"
            )
            is True
            and cash_count != 1
        ):
            errors.append(
                _issue(
                    "CASH_COMPARATOR_REQUIRED",
                    "资本竞争必须恰好包含一个现金比较项",
                    "$.capital_competition.ranked_uses",
                )
            )
        available_non_held = sum(
            symbol not in holding_by_symbol
            for symbol in candidate_by_symbol
        )
        minimum_non_held = min(
            int(
                competition_requirements.get(
                    "minimum_non_held_comparators",
                    0,
                )
            ),
            available_non_held,
        )
        non_held_count = sum(
            isinstance(item, Mapping)
            and item.get("comparator_type")
            == "non_held_candidate"
            for item in ranked_uses
        )
        if non_held_count < minimum_non_held:
            errors.append(
                _issue(
                    "NON_HELD_COMPARATORS_INSUFFICIENT",
                    "资本竞争缺少足够的非持仓候选",
                    "$.capital_competition.ranked_uses",
                )
            )
        ranked_symbols = {
            str(item.get("symbol", "")).upper()
            for item in ranked_uses
            if isinstance(item, Mapping)
            and item.get("symbol")
        }
        for index, item in enumerate(
            ranked_uses
        ):
            if not isinstance(item, Mapping):
                continue
            symbol = str(
                item.get("symbol", "")
            ).upper()
            comparator_type = item.get(
                "comparator_type"
            )
            current_position = item.get(
                "current_position"
            )
            if comparator_type == "current_holding":
                identity_matches = (
                    symbol in holding_by_symbol
                    and current_position is True
                )
            elif (
                comparator_type
                == "non_held_candidate"
            ):
                identity_matches = (
                    symbol in candidate_by_symbol
                    and symbol
                    not in holding_by_symbol
                    and current_position is False
                )
            else:
                identity_matches = (
                    comparator_type == "cash"
                    and current_position is False
                )
            if not identity_matches:
                errors.append(
                    _issue(
                        "CAPITAL_COMPARATOR_IDENTITY_MISMATCH",
                        f"{symbol or '<empty>'}资本比较身份与输入不一致",
                        (
                            "$.capital_competition"
                            ".ranked_uses"
                            f"[{index}]"
                        ),
                    )
                )
        if (
            competition_requirements.get(
                "require_top_ranked_non_held"
            )
            is True
        ):
            top_non_held = [
                symbol
                for symbol in candidate_by_symbol
                if symbol
                not in holding_by_symbol
            ][:minimum_non_held]
            missing_top_non_held = [
                symbol
                for symbol in top_non_held
                if symbol not in ranked_symbols
            ]
            if missing_top_non_held:
                errors.append(
                    _issue(
                        "TOP_NON_HELD_COMPARATORS_MISSING",
                        "缺少排名最高的非持仓比较项："
                        + ",".join(
                            missing_top_non_held
                        ),
                        "$.capital_competition.ranked_uses",
                    )
                )
        if (
            competition_requirements.get(
                "require_all_holdings_as_comparators"
            )
            is True
        ):
            missing_holding_uses = sorted(
                set(holding_by_symbol)
                - ranked_symbols
            )
            if missing_holding_uses:
                errors.append(
                    _issue(
                        "HOLDING_COMPARATORS_MISSING",
                        "资本竞争缺少现有持仓："
                        + ",".join(
                            missing_holding_uses
                        ),
                        "$.capital_competition.ranked_uses",
                    )
                )
        counterfactuals = competition.get(
            "holding_counterfactuals",
            [],
        )
        counterfactuals = (
            counterfactuals
            if isinstance(
                counterfactuals,
                list,
            )
            else []
        )
        counterfactual_symbols = [
            str(
                item.get("symbol", "")
            ).upper()
            for item in counterfactuals
            if isinstance(item, Mapping)
        ]
        counterfactual_by_symbol = {
            str(
                item.get("symbol", "")
            ).upper(): item
            for item in counterfactuals
            if isinstance(item, Mapping)
            and item.get("symbol")
        }
        if len(counterfactual_symbols) != len(
            set(counterfactual_symbols)
        ):
            errors.append(
                _issue(
                    "DUPLICATE_HOLDING_COUNTERFACTUAL",
                    "持仓反事实不能重复",
                    "$.capital_competition.holding_counterfactuals",
                )
            )
        if (
            competition_requirements.get(
                "require_counterfactual_for_each_holding"
            )
            is True
        ):
            missing_counterfactuals = sorted(
                set(holding_by_symbol)
                - set(counterfactual_by_symbol)
            )
            if missing_counterfactuals:
                errors.append(
                    _issue(
                        "HOLDING_COUNTERFACTUAL_MISSING",
                        "缺少持仓反事实："
                        + ",".join(
                            missing_counterfactuals
                        ),
                        "$.capital_competition.holding_counterfactuals",
                    )
                )
        if (
            competition_requirements.get(
                "increase_requires_would_buy_if_not_held"
            )
            is True
        ):
            for index, decision in enumerate(
                decisions
            ):
                if (
                    isinstance(
                        decision,
                        Mapping,
                    )
                    and decision.get("action")
                    == "increase"
                ):
                    symbol = str(
                        decision.get(
                            "symbol",
                            "",
                        )
                    ).upper()
                    counterfactual = (
                        counterfactual_by_symbol.get(
                            symbol,
                            {},
                        )
                    )
                    if (
                        counterfactual.get(
                            "would_buy_if_not_held"
                        )
                        is not True
                    ):
                        errors.append(
                            _issue(
                                "INCREASE_COUNTERFACTUAL_MISMATCH",
                                f"{symbol} increase要求未持有时仍会买入",
                                (
                                    "$.decisions"
                                    f"[{index}].action"
                                ),
                            )
                        )

        hysteresis_policy = policy.get(
            "holding_hysteresis", {}
        )
        hysteresis_policy = (
            hysteresis_policy
            if isinstance(hysteresis_policy, Mapping)
            else {}
        )
        maximum_review_days = int(
            hysteresis_policy.get("maximum_review_days", 0)
        )
        for index, counterfactual in enumerate(counterfactuals):
            if not isinstance(counterfactual, Mapping):
                continue
            if not hysteresis_policy:
                continue
            path = (
                "$.capital_competition."
                f"holding_counterfactuals[{index}]"
            )
            switching_cost = _decimal_or_zero(
                counterfactual.get(
                    "estimated_switching_cost_fraction"
                )
            )
            if switching_cost < ZERO or switching_cost > ONE:
                errors.append(
                    _issue(
                        "SWITCHING_COST_INVALID",
                        "换仓成本比例必须在0到1之间",
                        f"{path}.estimated_switching_cost_fraction",
                    )
                )
            review_day = _parse_date(
                counterfactual.get("review_by")
            )
            if (
                run_day is None
                or review_day is None
                or review_day < run_day
                or review_day
                > run_day + timedelta(days=maximum_review_days)
            ):
                errors.append(
                    _issue(
                        "HOLDING_REVIEW_DATE_INVALID",
                        "持仓反事实复核日期超过策略期限",
                        f"{path}.review_by",
                    )
                )
            if counterfactual.get("would_buy_if_not_held") is False:
                unresolved = counterfactual.get(
                    "unresolved_evidence", []
                )
                if not isinstance(unresolved, list) or not unresolved:
                    errors.append(
                        _issue(
                            "HOLDING_UNRESOLVED_EVIDENCE_REQUIRED",
                            "不会重新买入的持仓必须列出待解决证据",
                            f"{path}.unresolved_evidence",
                        )
                    )
                if (
                    hysteresis_policy.get(
                        "require_exit_or_reduce_if_evidence_unresolved"
                    )
                    is True
                    and counterfactual.get(
                        "exit_or_reduce_if_unresolved"
                    )
                    is not True
                ):
                    errors.append(
                        _issue(
                            "HOLDING_UNRESOLVED_ACTION_REQUIRED",
                            "待解决证据到期仍缺失时必须承诺减仓或退出",
                            f"{path}.exit_or_reduce_if_unresolved",
                        )
                    )

    if (
        competition_requirements.get(
            "require_etf_lookthrough_for_held_or_positive"
        )
        is True
    ):
        positive_symbols = {
            str(
                decision.get("symbol", "")
            ).upper()
            for decision in decisions
            if isinstance(decision, Mapping)
            and _decimal_or_zero(
                decision.get("target_weight")
            )
            > ZERO
        }
        required_etfs = {
            symbol
            for symbol, candidate
            in candidate_by_symbol.items()
            if candidate.get("asset_type")
            == "etf"
            and (
                symbol in holding_by_symbol
                or symbol in positive_symbols
            )
        }
        lookthrough = payload.get(
            "etf_lookthrough",
            {},
        )
        lookthrough = (
            lookthrough
            if isinstance(lookthrough, Mapping)
            else {}
        )
        assessments = lookthrough.get(
            "assessments",
            [],
        )
        assessed_symbols = {
            str(
                item.get("symbol", "")
            ).upper()
            for item in (
                assessments
                if isinstance(
                    assessments,
                    list,
                )
                else []
            )
            if isinstance(item, Mapping)
        }
        missing_etfs = sorted(
            required_etfs - assessed_symbols
        )
        if (
            missing_etfs
            and lookthrough.get("status")
            != "unavailable"
        ):
            errors.append(
                _issue(
                    "ETF_LOOKTHROUGH_MISSING",
                    "缺少ETF穿透研究："
                    + ",".join(missing_etfs),
                    "$.etf_lookthrough.assessments",
                )
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
    emerging_policy = policy.get(
        "emerging_growth_watchlist"
    )
    emerging_policy = (
        emerging_policy
        if isinstance(
            emerging_policy,
            Mapping,
        )
        else {}
    )
    emerging_source = str(
        emerging_policy.get(
            "source_name",
            "watchlist_non_sp500",
        )
    )
    emerging_initial_maximum = (
        _decimal_or_zero(
            emerging_policy.get(
                "maximum_initial_target_weight",
                "0",
            )
        )
    )
    emerging_aggregate_maximum = (
        _decimal_or_zero(
            emerging_policy.get(
                "maximum_aggregate_target_weight",
                "0",
            )
        )
    )
    emerging_positive_total = ZERO
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
            sector_key = sector
            sector_totals[sector_key] = (
                sector_totals.get(sector_key, ZERO)
                + target
            )
            if sector == "Unknown":
                warnings.append(
                    _issue(
                        "SECTOR_UNKNOWN",
                        f"{symbol}缺少行业分类，与其他未知行业合并计算上限",
                        f"{decision_path}.target_weight",
                    )
                )
        holding = holding_by_symbol.get(symbol)
        candidate = candidate_by_symbol.get(symbol)
        is_emerging_watchlist = (
            isinstance(candidate, Mapping)
            and str(
                candidate.get("source", "")
            )
            == emerging_source
        )
        if (
            is_emerging_watchlist
            and target > ZERO
        ):
            emerging_positive_total += target
            if (
                holding is None
                and emerging_initial_maximum > ZERO
                and target
                > emerging_initial_maximum
            ):
                errors.append(
                    _issue(
                        "EMERGING_INITIAL_WEIGHT_LIMIT_BREACHED",
                        (
                            f"{symbol}潜力成长观察池"
                            "初始目标权重超过策略上限"
                        ),
                        (
                            f"{decision_path}"
                            ".target_weight"
                        ),
                    )
                )
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

        valuation = raw_decision.get("valuation")
        valuation = (
            valuation
            if isinstance(valuation, Mapping)
            else {}
        )
        valuation_status = str(
            valuation.get("status", "")
        )
        valuation_numbers = {
            field: (
                None
                if valuation.get(field) is None
                else _decimal_or_zero(
                    valuation.get(field)
                )
            )
            for field in (
                "market_price",
                "value_range_low",
                "value_range_high",
                "margin_of_safety_fraction",
            )
        }
        valuation_research = policy.get(
            "valuation_research", {}
        )
        valuation_research = (
            valuation_research
            if isinstance(valuation_research, Mapping)
            else {}
        )
        attempted_methods = valuation.get(
            "attempted_methods", []
        )
        attempted_methods = (
            attempted_methods
            if isinstance(attempted_methods, list)
            else []
        )
        calculation_inputs = valuation.get(
            "calculation_inputs", []
        )
        calculation_inputs = (
            calculation_inputs
            if isinstance(calculation_inputs, list)
            else []
        )
        valuation_sources = valuation.get(
            "source_references", []
        )
        valuation_sources = (
            valuation_sources
            if isinstance(valuation_sources, list)
            else []
        )
        if valuation_status == "no_reliable_estimate":
            estimate_numbers = (
                valuation_numbers[
                    "value_range_low"
                ],
                valuation_numbers[
                    "value_range_high"
                ],
                valuation_numbers[
                    "margin_of_safety_fraction"
                ],
            )
            if any(
                value is not None
                for value in estimate_numbers
            ):
                errors.append(
                    _issue(
                        "UNRELIABLE_VALUATION_HAS_NUMBERS",
                        f"{symbol}无可靠估值时价值区间与安全边际必须为null",
                        f"{decision_path}.valuation",
                    )
                )
            market_price = valuation_numbers[
                "market_price"
            ]
            if (
                market_price is not None
                and market_price <= ZERO
            ):
                errors.append(
                    _issue(
                        "VALUATION_MARKET_PRICE_INVALID",
                        f"{symbol}市场参考价必须为正数或null",
                        (
                            f"{decision_path}."
                            "valuation.market_price"
                        ),
                    )
                )
            if (
                valuation.get("evidence_quality")
                != "insufficient"
            ):
                errors.append(
                    _issue(
                        "UNRELIABLE_VALUATION_EVIDENCE_MISMATCH",
                        f"{symbol}无可靠估值时证据质量必须为insufficient",
                        (
                            f"{decision_path}.valuation."
                            "evidence_quality"
                        ),
                    )
                )
            if valuation_research and len(attempted_methods) < int(
                valuation_research.get(
                    "minimum_attempted_methods_before_no_estimate",
                    0,
                )
            ):
                errors.append(
                    _issue(
                        "UNRELIABLE_VALUATION_METHODS_INSUFFICIENT",
                        f"{symbol}必须尝试足够的估值方法后才能无可靠估值",
                        f"{decision_path}.valuation.attempted_methods",
                    )
                )
            if valuation_research and not str(
                valuation.get("no_estimate_reason") or ""
            ).strip():
                errors.append(
                    _issue(
                        "UNRELIABLE_VALUATION_REASON_MISSING",
                        f"{symbol}无可靠估值必须说明具体缺失事实",
                        f"{decision_path}.valuation.no_estimate_reason",
                    )
                )
        elif valuation_status:
            market_price = valuation_numbers[
                "market_price"
            ]
            value_low = valuation_numbers[
                "value_range_low"
            ]
            value_high = valuation_numbers[
                "value_range_high"
            ]
            if (
                market_price is None
                or value_low is None
                or value_high is None
                or market_price <= ZERO
                or value_low <= ZERO
                or value_high < value_low
            ):
                errors.append(
                    _issue(
                        "VALUATION_RANGE_INVALID",
                        f"{symbol}估值价格或价值区间无效",
                        f"{decision_path}.valuation",
                    )
                )
            minimum_inputs = int(
                valuation_research.get(
                    "minimum_calculation_inputs_for_estimate",
                    0,
                )
            )
            if valuation_research and (
                len(calculation_inputs) < minimum_inputs
                or len(valuation_sources) < minimum_inputs
            ):
                errors.append(
                    _issue(
                        "VALUATION_EVIDENCE_INSUFFICIENT",
                        f"{symbol}估值缺少可复算输入或来源",
                        f"{decision_path}.valuation",
                    )
                )
            if (
                valuation_research
                and valuation.get("evidence_quality") == "insufficient"
            ):
                errors.append(
                    _issue(
                        "ESTIMATE_EVIDENCE_QUALITY_MISMATCH",
                        f"{symbol}已有估值区间时证据质量不能为insufficient",
                        f"{decision_path}.valuation.evidence_quality",
                    )
                )
            if (
                valuation_research
                and valuation.get("no_estimate_reason") is not None
            ):
                errors.append(
                    _issue(
                        "ESTIMATE_NO_REASON_MISMATCH",
                        f"{symbol}已有估值区间时no_estimate_reason必须为null",
                        f"{decision_path}.valuation.no_estimate_reason",
                    )
                )

        quality_rank = {
            "insufficient": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        if valuation_research and action in {"open", "increase"}:
            required_quality = str(
                valuation_research.get(
                    "minimum_evidence_quality_for_open_or_increase",
                    "medium",
                )
            )
            if quality_rank.get(
                str(valuation.get("evidence_quality", "")), -1
            ) < quality_rank.get(required_quality, 2):
                errors.append(
                    _issue(
                        "ACTION_VALUATION_EVIDENCE_TOO_LOW",
                        f"{symbol}开仓或增仓的估值证据质量不足",
                        f"{decision_path}.valuation.evidence_quality",
                    )
                )

        expected_return = raw_decision.get(
            "expected_return"
        )
        expected_return = (
            expected_return
            if isinstance(
                expected_return,
                Mapping,
            )
            else {}
        )
        scenario_values = [
            (
                None
                if expected_return.get(field) is None
                else _decimal_or_zero(
                    expected_return.get(field)
                )
            )
            for field in (
                "bear_annualized",
                "base_annualized",
                "bull_annualized",
            )
        ]
        populated_scenarios = [
            value
            for value in scenario_values
            if value is not None
        ]
        if (
            populated_scenarios
            and len(populated_scenarios) != 3
        ):
            errors.append(
                _issue(
                    "EXPECTED_RETURN_SCENARIOS_INCOMPLETE",
                    f"{symbol}预期回报场景必须全部填写或全部为null",
                    f"{decision_path}.expected_return",
                )
            )
        elif len(populated_scenarios) == 3:
            bear, base, bull = populated_scenarios
            if not bear <= base <= bull:
                errors.append(
                    _issue(
                        "EXPECTED_RETURN_SCENARIOS_UNORDERED",
                        f"{symbol}回报场景必须满足bear<=base<=bull",
                        f"{decision_path}.expected_return",
                    )
                )
        if valuation_research and action in {"open", "increase"}:
            required_confidence = str(
                valuation_research.get(
                    "minimum_return_confidence_for_open_or_increase",
                    "medium",
                )
            )
            if quality_rank.get(
                str(expected_return.get("confidence", "")), -1
            ) < quality_rank.get(required_confidence, 2):
                errors.append(
                    _issue(
                        "ACTION_RETURN_CONFIDENCE_TOO_LOW",
                        f"{symbol}开仓或增仓的预期回报置信度不足",
                        f"{decision_path}.expected_return.confidence",
                    )
                )

        monitoring_policy = policy.get(
            "thesis_monitoring", {}
        )
        monitoring_policy = (
            monitoring_policy
            if isinstance(monitoring_policy, Mapping)
            else {}
        )
        monitoring = raw_decision.get("monitoring_plan", {})
        monitoring = (
            monitoring
            if isinstance(monitoring, Mapping)
            else {}
        )
        triggers = monitoring.get("triggers", [])
        triggers = triggers if isinstance(triggers, list) else []
        if monitoring_policy and len(triggers) < int(
            monitoring_policy.get("minimum_triggers_per_decision", 0)
        ):
            errors.append(
                _issue(
                    "MONITORING_TRIGGERS_INSUFFICIENT",
                    f"{symbol}缺少足够的可衡量监控触发器",
                    f"{decision_path}.monitoring_plan.triggers",
                )
            )
        monitoring_review = _parse_date(
            monitoring.get("review_by")
        )
        monitoring_days = int(
            monitoring_policy.get("maximum_review_days", 0)
        )
        if monitoring_policy and (
            run_day is None
            or monitoring_review is None
            or monitoring_review < run_day
            or monitoring_review
            > run_day + timedelta(days=monitoring_days)
        ):
            errors.append(
                _issue(
                    "MONITORING_REVIEW_DATE_INVALID",
                    f"{symbol}监控复核日期超过策略期限",
                    f"{decision_path}.monitoring_plan.review_by",
                )
            )

        accumulation = raw_decision.get(
            "accumulation_plan"
        )
        accumulation = (
            accumulation
            if isinstance(accumulation, Mapping)
            else {}
        )
        planned_fraction = _decimal_or_zero(
            accumulation.get(
                "planned_total_fraction"
            )
        )
        tranches = accumulation.get("tranches")
        tranches = (
            tranches
            if isinstance(tranches, list)
            else []
        )
        tranche_total = ZERO
        for tranche_index, raw_tranche in enumerate(
            tranches
        ):
            if not isinstance(
                raw_tranche,
                Mapping,
            ):
                continue
            fraction = _decimal_or_zero(
                raw_tranche.get("fraction")
            )
            tranche_total += fraction
            low = raw_tranche.get(
                "price_trigger_low"
            )
            high = raw_tranche.get(
                "price_trigger_high"
            )
            low_value = (
                None
                if low is None
                else _decimal_or_zero(low)
            )
            high_value = (
                None
                if high is None
                else _decimal_or_zero(high)
            )
            if (
                fraction <= ZERO
                or (
                    low_value is not None
                    and high_value is not None
                    and high_value < low_value
                )
            ):
                errors.append(
                    _issue(
                        "ACCUMULATION_TRANCHE_INVALID",
                        f"{symbol}分批建仓阶段无效",
                        (
                            f"{decision_path}."
                            "accumulation_plan.tranches"
                            f"[{tranche_index}]"
                        ),
                    )
                )
        style = str(accumulation.get("style", ""))
        if (
            is_emerging_watchlist
            and target > ZERO
            and holding is None
            and emerging_policy.get(
                "require_staged_entry"
            )
            is True
            and style != "staged"
        ):
            errors.append(
                _issue(
                    "EMERGING_STAGED_ENTRY_REQUIRED",
                    (
                        f"{symbol}潜力成长观察池"
                        "新配置必须分批建仓"
                    ),
                    (
                        f"{decision_path}."
                        "accumulation_plan.style"
                    ),
                )
            )
        if (
            planned_fraction < ZERO
            or planned_fraction > ONE
            or tranche_total != planned_fraction
            or (
                style in {"wait", "no_add"}
                and (
                    planned_fraction != ZERO
                    or bool(tranches)
                )
            )
        ):
            errors.append(
                _issue(
                    "ACCUMULATION_PLAN_INVALID",
                    f"{symbol}分批建仓比例或等待状态无效",
                    f"{decision_path}.accumulation_plan",
                )
            )
    if (
        emerging_aggregate_maximum > ZERO
        and emerging_positive_total
        > emerging_aggregate_maximum
    ):
        errors.append(
            _issue(
                "EMERGING_AGGREGATE_WEIGHT_LIMIT_BREACHED",
                "潜力成长观察池总目标权重超过策略上限",
                "$.decisions",
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
