"""Load and validate the six WA Trader v2 configuration documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from v2.exceptions import ConfigurationError
from v2.runtime import get_project_root, load_json_object


CONFIG_FILENAMES = (
    "system.json",
    "risk.json",
    "stages.json",
    "market_data.json",
    "order_policy.json",
    "universe.json",
)
SUPPORTED_SCHEMA_VERSION = "1.0"
REQUIRED_TIMEZONE = "America/New_York"
REQUIRED_CANDIDATE_COUNT = 60
MINIMUM_DAILY_BARS = 300


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _require(
    condition: bool,
    message: str,
    *,
    file_name: str,
    field: str | None = None,
) -> None:
    if condition:
        return

    details: dict[str, Any] = {"file": file_name}
    if field:
        details["field"] = field
    raise ConfigurationError(message, details=details)


def _number(
    payload: Mapping[str, Any],
    field: str,
    *,
    file_name: str,
) -> float:
    value = payload.get(field)
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool),
        f"{file_name}.{field}必须是数字",
        file_name=file_name,
        field=field,
    )
    return float(value)


def _integer(
    payload: Mapping[str, Any],
    field: str,
    *,
    file_name: str,
) -> int:
    value = payload.get(field)
    _require(
        isinstance(value, int)
        and not isinstance(value, bool),
        f"{file_name}.{field}必须是整数",
        file_name=file_name,
        field=field,
    )
    return int(value)


def _validate_common(
    file_name: str,
    payload: Mapping[str, Any],
) -> None:
    _require(
        payload.get("schema_version")
        == SUPPORTED_SCHEMA_VERSION,
        (
            f"{file_name}.schema_version必须为"
            f"{SUPPORTED_SCHEMA_VERSION}"
        ),
        file_name=file_name,
        field="schema_version",
    )
    _require(
        isinstance(payload.get("config_version"), str)
        and bool(str(payload["config_version"]).strip()),
        f"{file_name}.config_version不能为空",
        file_name=file_name,
        field="config_version",
    )


def _validate_system(payload: Mapping[str, Any]) -> None:
    file_name = "system.json"
    _validate_common(file_name, payload)
    _require(
        payload.get("timezone") == REQUIRED_TIMEZONE,
        f"system.json.timezone必须为{REQUIRED_TIMEZONE}",
        file_name=file_name,
        field="timezone",
    )
    _require(
        payload.get("trading_mode") == "paper",
        "system.json.trading_mode必须为paper",
        file_name=file_name,
        field="trading_mode",
    )
    _require(
        payload.get("allow_live") is False,
        "system.json.allow_live必须为false",
        file_name=file_name,
        field="allow_live",
    )
    _require(
        _integer(
            payload,
            "codex_retry_count",
            file_name=file_name,
        )
        in {0, 1},
        "system.json.codex_retry_count只能为0或1",
        file_name=file_name,
        field="codex_retry_count",
    )
    _require(
        _number(
            payload,
            "request_timeout_seconds",
            file_name=file_name,
        )
        > 0,
        "system.json.request_timeout_seconds必须大于0",
        file_name=file_name,
        field="request_timeout_seconds",
    )
    for field in (
        "runtime_root",
        "report_root",
        "codex_temp_root",
    ):
        value = payload.get(field)
        _require(
            isinstance(value, str)
            and bool(value.strip())
            and not Path(value).is_absolute()
            and ".." not in Path(value).parts,
            f"system.json.{field}必须是项目内相对路径",
            file_name=file_name,
            field=field,
        )
    expected_paths = {
        "runtime_root": "decision_runtime_v2",
        "report_root": "reports/v2/daily",
        "codex_temp_root": ".tmp/codex",
    }
    for field, expected in expected_paths.items():
        _require(
            payload.get(field) == expected,
            (
                f"system.json.{field}必须为"
                f"{expected}"
            ),
            file_name=file_name,
            field=field,
        )


def _validate_risk(payload: Mapping[str, Any]) -> None:
    file_name = "risk.json"
    _validate_common(file_name, payload)
    min_cash = _number(
        payload,
        "minimum_cash_weight",
        file_name=file_name,
    )
    max_symbol = _number(
        payload,
        "max_single_symbol_weight",
        file_name=file_name,
    )
    max_sector = _number(
        payload,
        "max_sector_weight",
        file_name=file_name,
    )
    _require(
        0 <= min_cash <= 1,
        "risk.json.minimum_cash_weight必须在0到1之间",
        file_name=file_name,
        field="minimum_cash_weight",
    )
    _require(
        0 < max_symbol <= 1,
        "risk.json.max_single_symbol_weight必须在0到1之间",
        file_name=file_name,
        field="max_single_symbol_weight",
    )
    _require(
        max_symbol <= max_sector <= 1,
        "行业上限必须不低于单标的上限且不大于1",
        file_name=file_name,
        field="max_sector_weight",
    )
    for field in (
        "minimum_order_value",
        "max_slippage_bps",
        "max_quote_age_seconds",
        "max_extended_hours_spread_bps",
        "max_extended_hours_slippage_bps",
    ):
        _require(
            _number(payload, field, file_name=file_name) > 0,
            f"risk.json.{field}必须大于0",
            file_name=file_name,
            field=field,
        )
    _require(
        payload.get("allow_short") is False,
        "v2初期risk.json.allow_short必须为false",
        file_name=file_name,
        field="allow_short",
    )
    for field in (
        "allow_new_positions_extended_hours",
        "allow_fractional_extended_hours",
    ):
        _require(
            payload.get(field) is True,
            f"risk.json.{field}必须为true",
            file_name=file_name,
            field=field,
        )


def _validate_stages(payload: Mapping[str, Any]) -> None:
    file_name = "stages.json"
    _validate_common(file_name, payload)
    _require(
        _integer(
            payload,
            "coarse_candidate_count",
            file_name=file_name,
        )
        == REQUIRED_CANDIDATE_COUNT,
        "stages.json.coarse_candidate_count必须恰好为60",
        file_name=file_name,
        field="coarse_candidate_count",
    )
    for field in (
        "portfolio_valid_minutes",
        "execution_valid_seconds",
        "available_cash_change_rebalance_bps",
    ):
        _require(
            _number(payload, field, file_name=file_name) > 0,
            f"stages.json.{field}必须大于0",
            file_name=file_name,
            field=field,
        )
    for field in (
        "force_full_invalidates_coarse",
        "same_day_coarse_reuse",
    ):
        _require(
            payload.get(field) is True,
            f"stages.json.{field}必须为true",
            file_name=file_name,
            field=field,
        )
    for field in (
        "coarse_stage_version",
        "coarse_screening_version",
        "coarse_prompt_version",
        "coarse_schema_version",
    ):
        value = payload.get(field)
        _require(
            isinstance(value, str)
            and bool(value.strip()),
            f"stages.json.{field}必须是非空字符串",
            file_name=file_name,
            field=field,
        )
    screening = payload.get("coarse_screening")
    _require(
        isinstance(screening, dict),
        "stages.json.coarse_screening必须是对象",
        file_name=file_name,
        field="coarse_screening",
    )
    assert isinstance(screening, dict)
    supported = screening.get(
        "supported_asset_types"
    )
    _require(
        isinstance(supported, list)
        and set(supported) == {"stock", "etf"},
        "粗筛只支持stock和etf",
        file_name=file_name,
        field=(
            "coarse_screening."
            "supported_asset_types"
        ),
    )
    minimum_bars = screening.get(
        "minimum_screening_daily_bars"
    )
    _require(
        isinstance(minimum_bars, int)
        and not isinstance(minimum_bars, bool)
        and minimum_bars >= 20,
        "粗筛最少日线数量不得低于20",
        file_name=file_name,
        field=(
            "coarse_screening."
            "minimum_screening_daily_bars"
        ),
    )
    for field in (
        "minimum_price",
        "minimum_adv20",
    ):
        value = screening.get(field)
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) > 0,
            f"coarse_screening.{field}必须大于0",
            file_name=file_name,
            field=f"coarse_screening.{field}",
        )


def _validate_market_data(
    payload: Mapping[str, Any],
) -> None:
    file_name = "market_data.json"
    _validate_common(file_name, payload)
    _require(
        _integer(
            payload,
            "minimum_daily_bars",
            file_name=file_name,
        )
        >= MINIMUM_DAILY_BARS,
        "market_data.json.minimum_daily_bars不得少于300",
        file_name=file_name,
        field="minimum_daily_bars",
    )
    retained = _integer(
        payload,
        "retained_daily_bars",
        file_name=file_name,
    )
    _require(
        retained
        >= _integer(
            payload,
            "minimum_daily_bars",
            file_name=file_name,
        ),
        "retained_daily_bars不得少于minimum_daily_bars",
        file_name=file_name,
        field="retained_daily_bars",
    )
    _require(
        _integer(
            payload,
            "initial_daily_lookback_days",
            file_name=file_name,
        )
        > retained,
        "initial_daily_lookback_days必须大于保留bar数",
        file_name=file_name,
        field="initial_daily_lookback_days",
    )
    _require(
        _integer(
            payload,
            "minute_bar_window",
            file_name=file_name,
        )
        > 0,
        "market_data.json.minute_bar_window必须大于0",
        file_name=file_name,
        field="minute_bar_window",
    )
    required_phases = {
        "before_market_open",
        "regular_session",
        "after_market_close",
        "overnight_session",
        "market_closed_weekend",
        "market_closed_holiday",
        "unknown",
    }
    phases = payload.get("market_phases")
    _require(
        isinstance(phases, list)
        and required_phases.issubset(
            {str(value) for value in phases}
        ),
        "market_data.json.market_phases缺少必需市场阶段",
        file_name=file_name,
        field="market_phases",
    )
    _require(
        payload.get("new_position_session_policy")
        == "broker_capability_when_allow_trade",
        "新仓时段策略必须由allow_trade和券商能力决定",
        file_name=file_name,
        field="new_position_session_policy",
    )
    _require(
        payload.get("daily_adjustment")
        in {"all", "raw", "split", "dividend"},
        "market_data.json.daily_adjustment不支持",
        file_name=file_name,
        field="daily_adjustment",
    )
    _require(
        payload.get("daily_feed") in {"sip", "iex"},
        "market_data.json.daily_feed不支持",
        file_name=file_name,
        field="daily_feed",
    )


def _validate_order_policy(
    payload: Mapping[str, Any],
) -> None:
    file_name = "order_policy.json"
    _validate_common(file_name, payload)
    _require(
        payload.get("paper_only") is True,
        "order_policy.json.paper_only必须为true",
        file_name=file_name,
        field="paper_only",
    )
    for field in (
        "allowed_order_types",
        "allowed_time_in_force",
    ):
        value = payload.get(field)
        _require(
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, str)
                and bool(item.strip())
                for item in value
            ),
            f"order_policy.json.{field}必须是非空字符串数组",
            file_name=file_name,
            field=field,
        )
    _require(
        payload.get("require_local_idempotency_key")
        is True,
        "order_policy必须启用本地幂等键",
        file_name=file_name,
        field="require_local_idempotency_key",
    )
    _require(
        payload.get("block_duplicate_open_orders")
        is True,
        "order_policy必须阻止重复挂单",
        file_name=file_name,
        field="block_duplicate_open_orders",
    )
    submission = payload.get("submission")
    _require(
        isinstance(submission, dict),
        "order_policy.json.submission必须是对象",
        file_name=file_name,
        field="submission",
    )
    assert isinstance(submission, dict)
    _require(
        submission.get("default_enabled") is False
        and submission.get("paper_only") is True,
        "订单提交必须默认关闭且仅允许paper",
        file_name=file_name,
        field="submission",
    )
    extended = payload.get("extended_hours")
    _require(
        isinstance(extended, dict),
        "order_policy.json.extended_hours必须是对象",
        file_name=file_name,
        field="extended_hours",
    )
    assert isinstance(extended, dict)
    _require(
        extended.get("enabled_when_allow_trade")
        is True,
        "扩展时段只能随allow_trade启用",
        file_name=file_name,
        field="extended_hours.enabled_when_allow_trade",
    )
    _require(
        extended.get("equity_order_type") == "limit",
        "扩展时段股票订单必须适配为limit",
        file_name=file_name,
        field="extended_hours.equity_order_type",
    )
    extended_tif = extended.get(
        "allowed_time_in_force"
    )
    _require(
        isinstance(extended_tif, list)
        and set(extended_tif).issubset(
            set(payload["allowed_time_in_force"])
        )
        and bool(extended_tif),
        "扩展时段TIF必须是全局允许TIF的非空子集",
        file_name=file_name,
        field="extended_hours.allowed_time_in_force",
    )
    for field in (
        "max_spread_bps",
        "max_quote_age_seconds",
    ):
        _require(
            isinstance(extended.get(field), (int, float))
            and not isinstance(
                extended.get(field),
                bool,
            )
            and float(extended[field]) > 0,
            f"extended_hours.{field}必须大于0",
            file_name=file_name,
            field=f"extended_hours.{field}",
        )


def _validate_universe(payload: Mapping[str, Any]) -> None:
    file_name = "universe.json"
    _validate_common(file_name, payload)
    for field in (
        "stock_pool_files",
        "etf_pool_files",
        "must_include",
        "excluded_symbols",
    ):
        value = payload.get(field)
        _require(
            isinstance(value, list)
            and all(
                isinstance(item, str)
                and bool(item.strip())
                for item in value
            ),
            f"universe.json.{field}必须是字符串数组",
            file_name=file_name,
            field=field,
        )
    overlap = set(payload["must_include"]) & set(
        payload["excluded_symbols"]
    )
    _require(
        not overlap,
        "universe.json必须覆盖与排除标的不能重叠",
        file_name=file_name,
    )


def _validate_universe_sources(
    project_root: Path,
    payload: Mapping[str, Any],
) -> None:
    for field in (
        "stock_pool_files",
        "etf_pool_files",
    ):
        for raw_path in payload[field]:
            relative = Path(raw_path)
            _require(
                not relative.is_absolute()
                and ".." not in relative.parts,
                (
                    f"universe.json.{field}只能引用"
                    "项目内相对路径"
                ),
                file_name="universe.json",
                field=field,
            )
            source_path = (
                project_root / relative
            ).resolve()
            try:
                source_path.relative_to(project_root)
            except ValueError as error:
                raise ConfigurationError(
                    "股票池路径越出项目根目录",
                    details={
                        "file": "universe.json",
                        "field": field,
                    },
                ) from error

            _require(
                source_path.is_file(),
                f"股票池文件不存在：{raw_path}",
                file_name="universe.json",
                field=field,
            )


VALIDATORS = {
    "system.json": _validate_system,
    "risk.json": _validate_risk,
    "stages.json": _validate_stages,
    "market_data.json": _validate_market_data,
    "order_policy.json": _validate_order_policy,
    "universe.json": _validate_universe,
}


@dataclass(frozen=True)
class V2Config:
    project_root: Path
    config_directory: Path
    documents: Mapping[str, Mapping[str, Any]]
    config_version: str
    signature: str

    @property
    def system(self) -> Mapping[str, Any]:
        return self.documents["system.json"]

    @property
    def risk(self) -> Mapping[str, Any]:
        return self.documents["risk.json"]

    @property
    def stages(self) -> Mapping[str, Any]:
        return self.documents["stages.json"]

    @property
    def market_data(self) -> Mapping[str, Any]:
        return self.documents["market_data.json"]

    @property
    def order_policy(self) -> Mapping[str, Any]:
        return self.documents["order_policy.json"]

    @property
    def universe(self) -> Mapping[str, Any]:
        return self.documents["universe.json"]


def load_config(
    *,
    project_root: Path | None = None,
    config_directory: Path | None = None,
) -> V2Config:
    """Load all v2 config atomically from the caller's perspective."""

    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else get_project_root()
    )
    directory = (
        config_directory.expanduser().resolve()
        if config_directory is not None
        else root / "config" / "v2"
    )

    documents: dict[str, Mapping[str, Any]] = {}
    for file_name in CONFIG_FILENAMES:
        path = directory / file_name
        try:
            payload = load_json_object(path)
        except (FileNotFoundError, ValueError) as error:
            raise ConfigurationError(
                f"无法加载v2配置：{path}；{error}",
                details={"file": file_name},
            ) from error

        VALIDATORS[file_name](payload)
        documents[file_name] = MappingProxyType(payload)

    versions = {
        str(document["config_version"])
        for document in documents.values()
    }
    _require(
        len(versions) == 1,
        "六个v2配置文件的config_version必须一致",
        file_name="config/v2",
        field="config_version",
    )
    config_version = versions.pop()
    _validate_universe_sources(
        root,
        documents["universe.json"],
    )
    extended_policy = documents[
        "order_policy.json"
    ]["extended_hours"]
    risk = documents["risk.json"]
    assert isinstance(extended_policy, dict)
    _require(
        float(
            extended_policy["max_spread_bps"]
        )
        <= float(
            risk[
                "max_extended_hours_spread_bps"
            ]
        ),
        "订单策略扩展时段价差上限不能高于风险上限",
        file_name="config/v2",
        field="extended_hours.max_spread_bps",
    )
    _require(
        float(
            extended_policy[
                "max_quote_age_seconds"
            ]
        )
        <= float(risk["max_quote_age_seconds"]),
        "订单策略报价年龄不能高于风险上限",
        file_name="config/v2",
        field="extended_hours.max_quote_age_seconds",
    )

    signature_payload = {
        name: dict(documents[name])
        for name in CONFIG_FILENAMES
    }
    immutable_documents = MappingProxyType(documents)
    return V2Config(
        project_root=root,
        config_directory=directory,
        documents=immutable_documents,
        config_version=config_version,
        signature=_sha256(signature_payload),
    )
