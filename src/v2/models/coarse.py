"""Deterministic inputs and screening for the Stage C coarse selection."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from v2.config import V2Config
from v2.data.daily_bars import (
    DailyBarStore,
    normalize_daily_bar,
)
from v2.data.universe import load_universe
from v2.runtime import (
    build_shared_data_paths,
    load_json_object,
    utc_now_iso,
)


COARSE_INPUT_SCHEMA_VERSION = "1.0"


class CoarseResearchStatus(StrEnum):
    SUCCESS = "success"
    SUCCESS_LOCAL_ONLY = "success_local_only"


@dataclass(frozen=True)
class CoarseUniverseItem:
    symbol: str
    name: str
    asset_type: str
    sector: str | None
    industry: str | None
    source: str
    must_include: bool
    currently_held: bool
    has_open_order: bool
    asset_status: dict[str, Any]
    daily_summary: dict[str, Any]
    data_quality: dict[str, Any]
    research_eligible: bool
    screen_new_position_eligible: bool

    def validate(self) -> None:
        if not self.symbol.strip():
            raise ValueError(
                "CoarseUniverseItem.symbol不能为空"
            )
        if self.asset_type not in {
            "stock",
            "etf",
        }:
            raise ValueError(
                "CoarseUniverseItem.asset_type不支持"
            )
        for field_name in (
            "must_include",
            "currently_held",
            "has_open_order",
            "research_eligible",
            "screen_new_position_eligible",
        ):
            if not isinstance(
                getattr(self, field_name),
                bool,
            ):
                raise TypeError(
                    f"{field_name}必须是布尔值"
                )
        for field_name in (
            "asset_status",
            "daily_summary",
            "data_quality",
        ):
            if not isinstance(
                getattr(self, field_name),
                dict,
            ):
                raise TypeError(
                    f"{field_name}必须是对象"
                )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type,
            "sector": self.sector,
            "industry": self.industry,
            "source": self.source,
            "must_include": self.must_include,
            "currently_held": (
                self.currently_held
            ),
            "has_open_order": (
                self.has_open_order
            ),
            "research_eligible": (
                self.research_eligible
            ),
            "screen_new_position_eligible": (
                self.screen_new_position_eligible
            ),
            "asset_status": dict(
                self.asset_status
            ),
            "daily_summary": dict(
                self.daily_summary
            ),
            "data_quality": dict(
                self.data_quality
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CoarseUniverseItem":
        item = cls(
            symbol=str(payload.get("symbol", "")),
            name=str(payload.get("name", "")),
            asset_type=str(
                payload.get("asset_type", "")
            ),
            sector=(
                str(payload["sector"])
                if payload.get("sector")
                is not None
                else None
            ),
            industry=(
                str(payload["industry"])
                if payload.get("industry")
                is not None
                else None
            ),
            source=str(payload.get("source", "")),
            must_include=payload.get(
                "must_include"
            ),
            currently_held=payload.get(
                "currently_held"
            ),
            has_open_order=payload.get(
                "has_open_order"
            ),
            research_eligible=payload.get(
                "research_eligible"
            ),
            screen_new_position_eligible=(
                payload.get(
                    "screen_new_position_eligible"
                )
            ),
            asset_status=dict(
                payload.get("asset_status", {})
            ),
            daily_summary=dict(
                payload.get("daily_summary", {})
            ),
            data_quality=dict(
                payload.get("data_quality", {})
            ),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class CoarseInput:
    schema_version: str
    stage: str
    run_date: str
    generated_at: str
    input_signature: str
    universe: tuple[CoarseUniverseItem, ...]
    must_include: tuple[str, ...]
    exclusions: tuple[str, ...]
    current_positions: tuple[
        dict[str, Any],
        ...,
    ]
    open_order_symbols: tuple[str, ...]
    market_context: dict[str, Any]
    data_quality: dict[str, Any]
    policy: dict[str, Any]
    screening_summary: dict[str, Any] = field(
        default_factory=dict
    )

    def validate(self) -> None:
        if (
            self.schema_version != "1.0"
            or self.stage != "coarse_selection"
        ):
            raise ValueError(
                "CoarseInput版本或stage错误"
            )
        if len(self.input_signature) != 64:
            raise ValueError(
                "CoarseInput.input_signature必须为SHA-256"
            )
        symbols = [
            item.symbol
            for item in self.universe
        ]
        if len(symbols) != len(set(symbols)):
            raise ValueError(
                "CoarseInput.universe symbol不能重复"
            )
        for item in self.universe:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "run_date": self.run_date,
            "generated_at": self.generated_at,
            "input_signature": (
                self.input_signature
            ),
            "universe": [
                item.to_dict()
                for item in self.universe
            ],
            "screening_summary": dict(
                self.screening_summary
            ),
            "must_include": list(
                self.must_include
            ),
            "exclusions": list(self.exclusions),
            "current_positions": [
                dict(item)
                for item in self.current_positions
            ],
            "open_order_symbols": list(
                self.open_order_symbols
            ),
            "market_context": dict(
                self.market_context
            ),
            "data_quality": dict(
                self.data_quality
            ),
            "policy": dict(self.policy),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CoarseInput":
        raw_universe = payload.get(
            "universe",
            [],
        )
        if not isinstance(raw_universe, list):
            raise TypeError(
                "CoarseInput.universe必须是数组"
            )
        value = cls(
            schema_version=str(
                payload.get("schema_version", "")
            ),
            stage=str(payload.get("stage", "")),
            run_date=str(
                payload.get("run_date", "")
            ),
            generated_at=str(
                payload.get("generated_at", "")
            ),
            input_signature=str(
                payload.get(
                    "input_signature",
                    "",
                )
            ),
            universe=tuple(
                CoarseUniverseItem.from_dict(
                    item
                )
                for item in raw_universe
                if isinstance(item, Mapping)
            ),
            must_include=tuple(
                str(value)
                for value in payload.get(
                    "must_include",
                    [],
                )
            ),
            exclusions=tuple(
                str(value)
                for value in payload.get(
                    "exclusions",
                    [],
                )
            ),
            current_positions=tuple(
                dict(value)
                for value in payload.get(
                    "current_positions",
                    [],
                )
                if isinstance(value, Mapping)
            ),
            open_order_symbols=tuple(
                str(value)
                for value in payload.get(
                    "open_order_symbols",
                    [],
                )
            ),
            market_context=dict(
                payload.get(
                    "market_context",
                    {},
                )
            ),
            data_quality=dict(
                payload.get("data_quality", {})
            ),
            policy=dict(
                payload.get("policy", {})
            ),
            screening_summary=dict(
                payload.get(
                    "screening_summary",
                    {},
                )
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class CoarseSelection:
    rank: int
    symbol: str
    asset_type: str
    sector: str | None
    industry: str | None
    research_eligible: bool
    screen_new_position_eligible: bool
    selection_reason: str
    main_risks: tuple[str, ...]
    key_factors: tuple[str, ...]
    source_references: tuple[str, ...]

    def validate(self) -> None:
        if not 1 <= self.rank <= 60:
            raise ValueError(
                "CoarseSelection.rank必须在1到60"
            )
        if (
            not self.symbol.strip()
            or self.asset_type
            not in {"stock", "etf"}
            or not self.selection_reason.strip()
        ):
            raise ValueError(
                "CoarseSelection字段不完整"
            )
        if not isinstance(
            self.research_eligible,
            bool,
        ) or not isinstance(
            self.screen_new_position_eligible,
            bool,
        ):
            raise TypeError(
                "CoarseSelection eligibility必须是布尔值"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "sector": self.sector,
            "industry": self.industry,
            "research_eligible": (
                self.research_eligible
            ),
            "screen_new_position_eligible": (
                self.screen_new_position_eligible
            ),
            "selection_reason": (
                self.selection_reason
            ),
            "main_risks": list(
                self.main_risks
            ),
            "key_factors": list(
                self.key_factors
            ),
            "source_references": list(
                self.source_references
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CoarseSelection":
        value = cls(
            rank=int(payload.get("rank", 0)),
            symbol=str(payload.get("symbol", "")),
            asset_type=str(
                payload.get("asset_type", "")
            ),
            sector=(
                str(payload["sector"])
                if payload.get("sector")
                is not None
                else None
            ),
            industry=(
                str(payload["industry"])
                if payload.get("industry")
                is not None
                else None
            ),
            research_eligible=payload.get(
                "research_eligible"
            ),
            screen_new_position_eligible=(
                payload.get(
                    "screen_new_position_eligible"
                )
            ),
            selection_reason=str(
                payload.get(
                    "selection_reason",
                    "",
                )
            ),
            main_risks=tuple(
                str(item)
                for item in payload.get(
                    "main_risks",
                    [],
                )
            ),
            key_factors=tuple(
                str(item)
                for item in payload.get(
                    "key_factors",
                    [],
                )
            ),
            source_references=tuple(
                str(item)
                for item in payload.get(
                    "source_references",
                    [],
                )
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class CoarseOutput:
    schema_version: str
    stage: str
    run_date: str
    generated_at: str
    input_signature: str
    status: CoarseResearchStatus
    network_research: dict[str, Any]
    market_summary: str
    selection_count: int
    selections: tuple[CoarseSelection, ...]
    warnings: tuple[str, ...]
    source_references: tuple[
        dict[str, Any],
        ...,
    ]

    def validate(self) -> None:
        if (
            self.schema_version != "1.0"
            or self.stage != "coarse_selection"
            or self.selection_count != 60
            or len(self.selections) != 60
        ):
            raise ValueError(
                "CoarseOutput结构或数量错误"
            )
        symbols = [
            item.symbol
            for item in self.selections
        ]
        ranks = [
            item.rank
            for item in self.selections
        ]
        if (
            len(set(symbols)) != 60
            or sorted(ranks)
            != list(range(1, 61))
        ):
            raise ValueError(
                "CoarseOutput symbol或rank错误"
            )
        for item in self.selections:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "run_date": self.run_date,
            "generated_at": self.generated_at,
            "input_signature": (
                self.input_signature
            ),
            "status": self.status.value,
            "network_research": dict(
                self.network_research
            ),
            "market_summary": self.market_summary,
            "selection_count": (
                self.selection_count
            ),
            "selections": [
                item.to_dict()
                for item in self.selections
            ],
            "warnings": list(self.warnings),
            "source_references": [
                dict(item)
                for item in self.source_references
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CoarseOutput":
        value = cls(
            schema_version=str(
                payload.get("schema_version", "")
            ),
            stage=str(payload.get("stage", "")),
            run_date=str(
                payload.get("run_date", "")
            ),
            generated_at=str(
                payload.get("generated_at", "")
            ),
            input_signature=str(
                payload.get(
                    "input_signature",
                    "",
                )
            ),
            status=CoarseResearchStatus(
                payload.get("status", "")
            ),
            network_research=dict(
                payload.get(
                    "network_research",
                    {},
                )
            ),
            market_summary=str(
                payload.get("market_summary", "")
            ),
            selection_count=int(
                payload.get(
                    "selection_count",
                    0,
                )
            ),
            selections=tuple(
                CoarseSelection.from_dict(item)
                for item in payload.get(
                    "selections",
                    [],
                )
                if isinstance(item, Mapping)
            ),
            warnings=tuple(
                str(item)
                for item in payload.get(
                    "warnings",
                    [],
                )
            ),
            source_references=tuple(
                dict(item)
                for item in payload.get(
                    "source_references",
                    [],
                )
                if isinstance(item, Mapping)
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class CoarseValidationResult:
    valid: bool
    schema_valid: bool
    business_valid: bool
    errors: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    checked_at: str = field(
        default_factory=utc_now_iso
    )

    def validate(self) -> None:
        if self.valid != (
            self.schema_valid
            and self.business_valid
        ):
            raise ValueError(
                "CoarseValidationResult状态不一致"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": "1.0",
            "stage": "coarse_selection",
            "valid": self.valid,
            "checked_at": self.checked_at,
            "schema_valid": self.schema_valid,
            "business_valid": (
                self.business_valid
            ),
            "errors": [
                dict(item)
                for item in self.errors
            ],
            "warnings": [
                dict(item)
                for item in self.warnings
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CoarseValidationResult":
        value = cls(
            valid=bool(payload.get("valid", False)),
            schema_valid=bool(
                payload.get(
                    "schema_valid",
                    False,
                )
            ),
            business_valid=bool(
                payload.get(
                    "business_valid",
                    False,
                )
            ),
            errors=tuple(
                dict(item)
                for item in payload.get(
                    "errors",
                    [],
                )
                if isinstance(item, Mapping)
            ),
            warnings=tuple(
                dict(item)
                for item in payload.get(
                    "warnings",
                    [],
                )
                if isinstance(item, Mapping)
            ),
            checked_at=str(
                payload.get(
                    "checked_at",
                    utc_now_iso(),
                )
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class CoarseInputBuildResult:
    payload: dict[str, Any]
    input_signature: str
    candidate_symbols: frozenset[str]
    latest_daily_date: str | None


def _sha256(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _mean(values: list[float]) -> float | None:
    return (
        sum(values) / len(values)
        if values
        else None
    )


def _return(
    closes: list[float],
    sessions: int,
) -> float | None:
    if len(closes) <= sessions:
        return None
    denominator = closes[-1 - sessions]
    if denominator <= 0:
        return None
    return closes[-1] / denominator - 1.0


def _volatility(
    closes: list[float],
    sessions: int,
) -> float | None:
    if len(closes) <= sessions:
        return None
    selected = closes[-(sessions + 1) :]
    returns = [
        selected[index] / selected[index - 1] - 1.0
        for index in range(1, len(selected))
        if selected[index - 1] > 0
    ]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(252.0)


def _distance_from_sma(
    closes: list[float],
    sessions: int,
) -> float | None:
    if len(closes) < sessions:
        return None
    average = _mean(closes[-sessions:])
    if average is None or average <= 0:
        return None
    return closes[-1] / average - 1.0


def _rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    changes = [
        closes[index] - closes[index - 1]
        for index in range(
            len(closes) - 14,
            len(closes),
        )
    ]
    gains = [
        max(change, 0.0)
        for change in changes
    ]
    losses = [
        max(-change, 0.0)
        for change in changes
    ]
    average_gain = _mean(gains)
    average_loss = _mean(losses)
    if average_gain is None or average_loss is None:
        return None
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def summarize_daily_bars(
    raw_bars: Iterable[object],
) -> tuple[dict[str, Any], list[str]]:
    """Return stable metrics; unavailable values are represented by ``None``."""

    bars: list[dict[str, Any]] = []
    invalid_count = 0
    for raw in raw_bars:
        normalized = normalize_daily_bar(raw)
        if normalized is None:
            invalid_count += 1
        else:
            bars.append(normalized)
    by_timestamp = {
        str(bar["timestamp"]): bar
        for bar in bars
    }
    bars = [
        by_timestamp[key]
        for key in sorted(by_timestamp)
    ]
    closes = [
        float(bar["close"])
        for bar in bars
    ]
    volumes = [
        float(bar["volume"])
        for bar in bars
    ]
    warnings: list[str] = []
    if invalid_count:
        warnings.append(
            "INVALID_DAILY_BARS_IGNORED:"
            f"{invalid_count}"
        )
    if len(by_timestamp) < len(bars):
        warnings.append(
            "DUPLICATE_DAILY_BARS_DEDUPLICATED"
        )

    last_close = closes[-1] if closes else None
    dollar_volumes = [
        closes[index] * volumes[index]
        for index in range(len(closes))
    ]
    adv20 = (
        _mean(dollar_volumes[-20:])
        if len(dollar_volumes) >= 20
        else None
    )
    high_52w = (
        max(closes[-252:])
        if closes
        else None
    )
    drawdown = (
        last_close / high_52w - 1.0
        if last_close is not None
        and high_52w is not None
        and high_52w > 0
        else None
    )
    previous_volume_average = (
        _mean(volumes[-21:-1])
        if len(volumes) >= 21
        else None
    )
    volume_ratio = (
        volumes[-1] / previous_volume_average
        if volumes
        and previous_volume_average is not None
        and previous_volume_average > 0
        else None
    )
    last_timestamp = (
        str(bars[-1]["timestamp"])
        if bars
        else None
    )
    last_bar_date = (
        last_timestamp[:10]
        if last_timestamp
        else None
    )
    return (
        {
            "bars_available": len(bars),
            "last_bar_date": last_bar_date,
            "last_close": last_close,
            "average_dollar_volume_20d": adv20,
            "return_5d": _return(closes, 5),
            "return_20d": _return(closes, 20),
            "return_60d": _return(closes, 60),
            "return_252d": _return(closes, 252),
            "volatility_20d": _volatility(
                closes,
                20,
            ),
            "volatility_60d": _volatility(
                closes,
                60,
            ),
            "drawdown_from_52w_high": drawdown,
            "distance_from_sma_20": _distance_from_sma(
                closes,
                20,
            ),
            "distance_from_sma_50": _distance_from_sma(
                closes,
                50,
            ),
            "distance_from_sma_200": _distance_from_sma(
                closes,
                200,
            ),
            "rsi_14": _rsi14(closes),
            "volume_ratio_20d": volume_ratio,
        },
        warnings,
    )


def _asset_map(
    project_root: Path,
    base_snapshot: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    snapshot_paths = (
        build_shared_data_paths(
            project_root=project_root
        ).assets
        / "assets.json",
        project_root
        / "data"
        / "snapshots"
        / "assets.json",
    )
    for snapshot_path in snapshot_paths:
        if not snapshot_path.exists():
            continue
        try:
            payload = load_json_object(snapshot_path)
            for raw in payload.get("assets", []):
                if not isinstance(raw, dict):
                    continue
                symbol = str(
                    raw.get("symbol", "")
                ).strip().upper()
                if symbol:
                    result[symbol] = dict(raw)
        except (OSError, ValueError):
            pass
    for raw in base_snapshot.get("assets", []):
        if not isinstance(raw, dict):
            continue
        symbol = str(
            raw.get("symbol", "")
        ).strip().upper()
        if symbol:
            merged = dict(result.get(symbol, {}))
            merged.update(raw)
            result[symbol] = merged
    return result


def _symbol_set(
    values: Iterable[object],
) -> set[str]:
    return {
        str(value).strip().upper()
        for value in values
        if str(value).strip()
    }


def _symbols_from_records(
    records: object,
) -> set[str]:
    if not isinstance(records, list):
        return set()
    return {
        str(record.get("symbol", "")).strip().upper()
        for record in records
        if isinstance(record, dict)
        and str(record.get("symbol", "")).strip()
    }


def _position_summary(
    records: object,
) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        return []
    summaries: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        symbol = str(
            record.get("symbol", "")
        ).strip().upper()
        if not symbol:
            continue
        summaries.append(
            {
                "symbol": symbol,
                "side": record.get("side"),
                "quantity": record.get("quantity"),
                "average_entry_price": (
                    record.get("average_entry_price")
                ),
            }
        )
    return sorted(
        summaries,
        key=lambda item: item["symbol"],
    )


def _asset_status(
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": metadata.get("status"),
        "tradable": metadata.get("tradable"),
        "fractionable": metadata.get(
            "fractionable"
        ),
        "shortable": metadata.get("shortable"),
        "exchange": metadata.get("exchange"),
    }


def _screen_reasons(
    *,
    asset_type: str,
    asset: Mapping[str, Any],
    summary: Mapping[str, Any],
    screening: Mapping[str, Any],
    bar_warnings: list[str],
) -> list[str]:
    reasons: list[str] = []
    supported = set(
        str(value)
        for value in screening[
            "supported_asset_types"
        ]
    )
    if asset_type not in supported:
        reasons.append("UNSUPPORTED_ASSET_TYPE")
    status = str(
        asset.get("status", "")
    ).strip().lower()
    if status and status != "active":
        reasons.append("ASSET_INACTIVE_OR_HALTED")
    if asset.get("tradable") is False:
        reasons.append("ASSET_NOT_TRADABLE")
    bars_available = int(
        summary["bars_available"]
    )
    if bars_available < int(
        screening[
            "minimum_screening_daily_bars"
        ]
    ):
        reasons.append("SEVERE_DAILY_HISTORY_SHORTAGE")
    invalid_count = sum(
        int(value.rsplit(":", 1)[-1])
        for value in bar_warnings
        if value.startswith(
            "INVALID_DAILY_BARS_IGNORED:"
        )
    )
    if invalid_count > max(2, bars_available // 10):
        reasons.append("DAMAGED_DAILY_BAR_SERIES")
    last_close = summary.get("last_close")
    if (
        isinstance(last_close, (int, float))
        and float(last_close)
        < float(screening["minimum_price"])
    ):
        reasons.append("PRICE_BELOW_MINIMUM")
    adv20 = summary.get(
        "average_dollar_volume_20d"
    )
    if (
        isinstance(adv20, (int, float))
        and float(adv20)
        < float(screening["minimum_adv20"])
    ):
        reasons.append("LIQUIDITY_BELOW_MINIMUM")
    return reasons


def build_coarse_input(
    *,
    config: V2Config,
    run_date: str,
    base_snapshot: Mapping[str, Any],
    bar_store: DailyBarStore | None = None,
    generated_at: str | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
    guidance: Mapping[str, Any] | None = None,
) -> CoarseInputBuildResult:
    """Build a coarse input without including cash, quotes, or cycle identity."""

    universe = load_universe(config)
    store = bar_store or DailyBarStore.for_project(
        config.project_root
    )
    assets = _asset_map(
        config.project_root,
        base_snapshot,
    )
    positions = base_snapshot.get("positions", [])
    open_orders = base_snapshot.get(
        "open_orders",
        [],
    )
    held_symbols = _symbols_from_records(positions)
    open_order_symbols = _symbols_from_records(
        open_orders
    )
    must_include = _symbol_set(
        universe["must_include"]
    )
    exclusions = _symbol_set(
        universe["exclusions"]
    )
    preserved = (
        held_symbols
        | open_order_symbols
        | must_include
    )
    screening = config.stages[
        "coarse_screening"
    ]
    assert isinstance(screening, Mapping)

    candidates: list[dict[str, Any]] = []
    excluded_records: list[dict[str, Any]] = []
    latest_dates: list[str] = []
    top_warnings: list[str] = []
    seen: set[str] = set()
    configured_entries = [
        dict(entry)
        for entry in universe["entries"]
    ]
    configured_symbols = {
        str(entry["symbol"])
        for entry in configured_entries
    }
    dynamic_symbols = sorted(
        (
            held_symbols
            | open_order_symbols
        )
        - configured_symbols
    )
    entries_to_process = [
        *configured_entries,
        *[
            {
                "symbol": symbol,
                "asset_type": "stock",
                "sources": [
                    "current_position_or_order"
                ],
                "_dynamic_account_symbol": True,
            }
            for symbol in dynamic_symbols
        ],
    ]
    for entry in entries_to_process:
        symbol = str(entry["symbol"])
        if symbol in seen:
            excluded_records.append(
                {
                    "symbol": symbol,
                    "reasons": ["DUPLICATE_SYMBOL"],
                }
            )
            continue
        seen.add(symbol)
        asset_type = str(entry["asset_type"])
        raw_bars = store.bars(symbol)
        daily_summary, bar_warnings = (
            summarize_daily_bars(raw_bars)
        )
        if (
            daily_summary["last_bar_date"]
            and not entry.get(
                "_dynamic_account_symbol",
                False,
            )
        ):
            latest_dates.append(
                str(
                    daily_summary[
                        "last_bar_date"
                    ]
                )
            )
        asset = assets.get(symbol, {})
        reasons = _screen_reasons(
            asset_type=asset_type,
            asset=asset,
            summary=daily_summary,
            screening=screening,
            bar_warnings=bar_warnings,
        )
        if symbol in exclusions:
            reasons.append("CONFIG_EXCLUSION")
        is_preserved = (
            symbol in preserved
            and symbol not in exclusions
        )
        hard_excluded = bool(reasons)
        if hard_excluded and not is_preserved:
            excluded_records.append(
                {
                    "symbol": symbol,
                    "reasons": sorted(set(reasons)),
                }
            )
            continue
        item_warnings = list(bar_warnings)
        if not asset:
            item_warnings.append(
                "ASSET_METADATA_UNAVAILABLE"
            )
        if reasons and is_preserved:
            item_warnings.extend(
                f"PRESERVED_OVERRIDE:{reason}"
                for reason in sorted(set(reasons))
            )
        sources = (
            entry.get("sources", [])
            if isinstance(
                entry.get("sources", []),
                list,
            )
            else []
        )
        source = (
            Path(str(sources[0])).stem
            if sources
            else "configured_universe"
        )
        candidates.append(
            {
                "symbol": symbol,
                "name": (
                    str(asset.get("name"))
                    if asset.get("name")
                    else symbol
                ),
                "asset_type": asset_type,
                "sector": asset.get("sector"),
                "industry": asset.get("industry"),
                "source": source,
                "must_include": (
                    symbol in must_include
                ),
                "currently_held": (
                    symbol in held_symbols
                ),
                "has_open_order": (
                    symbol in open_order_symbols
                ),
                "research_eligible": not bool(
                    reasons
                ),
                "screen_new_position_eligible": (
                    not bool(reasons)
                    and asset.get("tradable")
                    is True
                ),
                "asset_status": _asset_status(
                    asset
                ),
                "daily_summary": daily_summary,
                "data_quality": {
                    "asset_metadata_available": (
                        bool(asset)
                    ),
                    "daily_history_sufficient": (
                        int(
                            daily_summary[
                                "bars_available"
                            ]
                        )
                        >= int(
                            screening[
                                "minimum_screening_daily_bars"
                            ]
                        )
                    ),
                    "warnings": sorted(
                        set(item_warnings)
                    ),
                },
            }
        )

    latest_daily_date = (
        max(latest_dates)
        if latest_dates
        else None
    )
    guidance_payload = (
        dict(guidance)
        if isinstance(guidance, Mapping)
        else {
            "mode": "skipped_by_flag",
            "raw_text": "",
            "guidance_hash": (
                "e3b0c44298fc1c149afbf4c8996fb924"
                "27ae41e4649b934ca495991b7852b855"
            ),
            "applies_to": [
                "coarse",
                "portfolio",
                "execution",
            ],
        }
    )
    signature_payload = {
        "run_date": run_date,
        "profile_id": profile_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "guidance_hash": str(
            guidance_payload.get(
                "guidance_hash",
                "",
            )
        ),
        "stock_pool_version": (
            universe["stock_pool_version"]
        ),
        "etf_pool_version": (
            universe["etf_pool_version"]
        ),
        "symbols": universe["symbols"],
        "must_include": sorted(must_include),
        "exclusions": sorted(exclusions),
        "latest_daily_date": latest_daily_date,
        "screening_config_version": (
            config.stages[
                "coarse_screening_version"
            ]
        ),
        "stages_config_version": (
            config.stages[
                "coarse_stage_version"
            ]
        ),
        "prompt_version": (
            config.stages[
                "coarse_prompt_version"
            ]
        ),
        "schema_version": (
            config.stages[
                "coarse_schema_version"
            ]
        ),
    }
    input_signature = _sha256(
        signature_payload
    )
    if len(candidates) < int(
        config.stages["coarse_candidate_count"]
    ):
        top_warnings.append(
            "COARSE_ELIGIBLE_UNIVERSE_BELOW_REQUIRED_COUNT"
        )
    missing_sectors = sum(
        candidate["sector"] is None
        for candidate in candidates
        if candidate["asset_type"] == "stock"
    )
    if missing_sectors:
        top_warnings.append(
            "STOCK_SECTOR_METADATA_PARTIAL:"
            f"{missing_sectors}"
        )
    if (
        latest_daily_date is not None
        and latest_daily_date < run_date
    ):
        top_warnings.append(
            "DAILY_DATA_LATEST_DATE_BEFORE_RUN_DATE:"
            f"{latest_daily_date}"
        )
    generated = generated_at or utc_now_iso()
    payload: dict[str, Any] = {
        "schema_version": (
            COARSE_INPUT_SCHEMA_VERSION
        ),
        "stage": "coarse_selection",
        "run_date": run_date,
        "profile_id": profile_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "generated_at": generated,
        "input_signature": input_signature,
        "initial_guidance": {
            "mode": str(
                guidance_payload.get(
                    "mode",
                    "skipped_by_flag",
                )
            ),
            "raw_text": str(
                guidance_payload.get(
                    "raw_text",
                    "",
                )
            ),
            "guidance_hash": str(
                guidance_payload.get(
                    "guidance_hash",
                    "",
                )
            ),
            "applies_to": list(
                guidance_payload.get(
                    "applies_to",
                    [
                        "coarse",
                        "portfolio",
                        "execution",
                    ],
                )
            ),
            "semantics": (
                "research_preference_not_trade_mandate"
            ),
        },
        "universe": candidates,
        "screening_summary": {
            "input_count": (
                len(entries_to_process)
            ),
            "eligible_count": len(candidates),
            "excluded_count": len(
                excluded_records
            ),
            "exclusions": [
                {
                    "symbol": item["symbol"],
                    "reason_code": (
                        item["reasons"][0]
                    ),
                    "reason": ";".join(
                        item["reasons"]
                    ),
                }
                for item in excluded_records
            ],
            "stock_pool_version": (
                universe["stock_pool_version"]
            ),
            "etf_pool_version": (
                universe["etf_pool_version"]
            ),
        },
        "must_include": sorted(must_include),
        "exclusions": sorted(exclusions),
        "current_positions": _position_summary(
            positions
        ),
        "open_order_symbols": sorted(
            open_order_symbols
        ),
        "market_context": {
            "market_phase": base_snapshot.get(
                "market_phase",
                "unknown",
            ),
            "latest_daily_date": (
                latest_daily_date
            ),
        },
        "data_quality": {
            "candidate_count_sufficient": (
                len(candidates)
                >= int(
                    config.stages[
                        "coarse_candidate_count"
                    ]
                )
            ),
            "warnings": top_warnings,
        },
        "policy": {
            "required_selection_count": int(
                config.stages[
                    "coarse_candidate_count"
                ]
            ),
            "must_include_all": True,
            "unique_symbols_required": True,
            "orders_forbidden": True,
            "weights_forbidden": True,
            "quantities_forbidden": True,
            "web_research_allowed": True,
            "screening_version": (
                config.stages[
                    "coarse_screening_version"
                ]
            ),
            "prompt_version": (
                config.stages[
                    "coarse_prompt_version"
                ]
            ),
            "output_schema_version": (
                config.stages[
                    "coarse_schema_version"
                ]
            ),
        },
    }
    normalized_payload = (
        CoarseInput.from_dict(payload).to_dict()
    )
    return CoarseInputBuildResult(
        payload=normalized_payload,
        input_signature=input_signature,
        candidate_symbols=frozenset(
            candidate["symbol"]
            for candidate in candidates
        ),
        latest_daily_date=latest_daily_date,
    )
