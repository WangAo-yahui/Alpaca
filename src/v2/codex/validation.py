"""Schema preflight and business validation for coarse-selection output."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import (
    Draft202012Validator,
    SchemaError,
)

from v2.exceptions import ConfigurationError
from v2.models.coarse import (
    CoarseValidationResult,
)
from v2.runtime import load_json_object


FORBIDDEN_OUTPUT_FIELDS = {
    "new_position_allowed",
    "target_weight",
    "order",
    "orders",
    "quantity",
}
FORBIDDEN_SCHEMA_COMBINATORS = {
    "oneOf",
    "anyOf",
    "allOf",
    "format",
    "if",
    "then",
    "else",
    "dependentSchemas",
}


def load_coarse_schema(path: Path) -> dict[str, Any]:
    return load_json_object(path)


def _walk_schema(
    node: object,
    path: tuple[str, ...] = (),
) -> Iterable[tuple[tuple[str, ...], object]]:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_schema(
                value,
                (*path, str(key)),
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_schema(
                value,
                (*path, str(index)),
            )


def preflight_output_schema(
    schema: Mapping[str, Any],
) -> None:
    """Reject schema constructs that Codex structured output cannot trust."""

    try:
        Draft202012Validator.check_schema(
            schema
        )
    except SchemaError as error:
        raise ConfigurationError(
            "粗选输出Schema不是有效的JSON Schema",
            code="COARSE_SCHEMA_INVALID",
            details={"reason": error.message},
        ) from error

    problems: list[str] = []
    for path, node in _walk_schema(schema):
        if not isinstance(node, dict):
            continue
        for keyword in FORBIDDEN_SCHEMA_COMBINATORS:
            if keyword in node:
                problems.append(
                    f"{'.'.join(path) or '$'}:"
                    f"{keyword}"
                )
        node_type = node.get("type")
        if node_type == "object":
            properties = node.get("properties")
            required = node.get("required")
            if node.get("additionalProperties") is not False:
                problems.append(
                    f"{'.'.join(path) or '$'}:"
                    "additionalProperties"
                )
            if not isinstance(properties, dict):
                problems.append(
                    f"{'.'.join(path) or '$'}:"
                    "properties"
                )
            elif (
                not isinstance(required, list)
                or set(required) != set(properties)
            ):
                problems.append(
                    f"{'.'.join(path) or '$'}:"
                    "required"
                )
    if problems:
        raise ConfigurationError(
            "粗选输出Schema不满足严格对象约束",
            code="COARSE_SCHEMA_PREFLIGHT_FAILED",
            details={
                "problems": sorted(problems)
            },
        )


def _issue(
    code: str,
    message: str,
    *,
    path: str = "$",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "path": path,
    }


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


def _forbidden_paths(
    node: object,
    path: str = "$",
) -> list[str]:
    paths: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_OUTPUT_FIELDS:
                paths.append(child)
            paths.extend(
                _forbidden_paths(value, child)
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            paths.extend(
                _forbidden_paths(
                    value,
                    f"{path}[{index}]",
                )
            )
    return paths


def validate_coarse_output(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    now: datetime | None = None,
) -> CoarseValidationResult:
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
                path=path,
            )
        )

    if payload.get("stage") != "coarse_selection":
        errors.append(
            _issue(
                "STAGE_MISMATCH",
                "stage必须为coarse_selection",
                path="$.stage",
            )
        )
    if payload.get("run_date") != input_payload.get(
        "run_date"
    ):
        errors.append(
            _issue(
                "RUN_DATE_MISMATCH",
                "输出run_date与输入不一致",
                path="$.run_date",
            )
        )
    if payload.get(
        "input_signature"
    ) != input_payload.get("input_signature"):
        errors.append(
            _issue(
                "INPUT_SIGNATURE_MISMATCH",
                "输出input_signature与输入不一致",
                path="$.input_signature",
            )
        )
    if payload.get("status") not in {
        "success",
        "success_local_only",
    }:
        errors.append(
            _issue(
                "INVALID_STATUS",
                "输出状态不是允许的成功状态",
                path="$.status",
            )
        )

    selections = payload.get("selections")
    if not isinstance(selections, list):
        selections = []
    if payload.get("selection_count") != 60:
        errors.append(
            _issue(
                "SELECTION_COUNT_NOT_60",
                "selection_count必须恰好为60",
                path="$.selection_count",
            )
        )
    if len(selections) != 60:
        errors.append(
            _issue(
                "SELECTION_LIST_NOT_60",
                "selections必须恰好包含60项",
                path="$.selections",
            )
        )

    universe = input_payload.get("universe", [])
    raw_items = (
        universe
        if isinstance(universe, list)
        else universe.get("items", [])
        if isinstance(universe, dict)
        else []
    )
    input_items = {
        str(item.get("symbol", "")).upper(): item
        for item in raw_items
        if isinstance(item, dict)
        and item.get("symbol")
    }
    selected_symbols: list[str] = []
    ranks: list[int] = []
    selection_origins: list[
        tuple[str, str, str, int]
    ] = []
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            continue
        path = f"$.selections[{index}]"
        symbol = str(
            selection.get("symbol", "")
        ).strip().upper()
        selected_symbols.append(symbol)
        selection_origins.append(
            (
                symbol,
                str(
                    selection.get(
                        "asset_type",
                        "",
                    )
                ),
                str(
                    selection.get(
                        "selection_origin",
                        "",
                    )
                ),
                index,
            )
        )
        rank = selection.get("rank")
        if isinstance(rank, int) and not isinstance(
            rank,
            bool,
        ):
            ranks.append(rank)
        candidate = input_items.get(symbol)
        if candidate is None:
            errors.append(
                _issue(
                    "SYMBOL_NOT_ELIGIBLE_INPUT",
                    f"{symbol or '<empty>'}不在粗选输入中",
                    path=f"{path}.symbol",
                )
            )
            continue
        if (
            selection.get("asset_type")
            != candidate.get("asset_type")
        ):
            errors.append(
                _issue(
                    "ASSET_TYPE_MISMATCH",
                    f"{symbol}的asset_type与输入不一致",
                    path=f"{path}.asset_type",
                )
            )
        for field in (
            "research_eligible",
            "screen_new_position_eligible",
        ):
            if not isinstance(
                selection.get(field),
                bool,
            ):
                errors.append(
                    _issue(
                        "ELIGIBILITY_FLAG_NOT_BOOLEAN",
                        f"{symbol}的{field}必须为布尔值",
                        path=f"{path}.{field}",
                    )
                )
            elif (
                selection.get(field)
                != candidate.get(field)
            ):
                errors.append(
                    _issue(
                        "ELIGIBILITY_FLAG_MISMATCH",
                        f"{symbol}的{field}与输入不一致",
                        path=f"{path}.{field}",
                    )
                )
        reason = selection.get(
            "selection_reason"
        )
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                _issue(
                    "EMPTY_SELECTION_REASON",
                    f"{symbol}缺少非空selection_reason",
                    path=(
                        f"{path}.selection_reason"
                    ),
                )
            )

    input_policy = input_payload.get(
        "policy",
        {},
    )
    input_policy = (
        input_policy
        if isinstance(input_policy, Mapping)
        else {}
    )
    supplement_policy = input_policy.get(
        "codex_supplement_selection",
        {},
    )
    supplement_policy = (
        supplement_policy
        if isinstance(
            supplement_policy,
            Mapping,
        )
        else {}
    )
    shortlists = input_payload.get(
        "python_shortlists",
        {},
    )
    shortlists = (
        shortlists
        if isinstance(shortlists, Mapping)
        else {}
    )
    shortlist_sets = {
        asset_type: {
            str(item.get("symbol", ""))
            .strip()
            .upper()
            for item in (
                shortlists.get(asset_type, [])
                if isinstance(
                    shortlists.get(asset_type, []),
                    list,
                )
                else []
            )
            if isinstance(item, Mapping)
            and item.get("symbol")
        }
        for asset_type in ("stock", "etf")
    }
    supplement_count = 0
    if supplement_policy:
        for (
            symbol,
            asset_type,
            origin,
            index,
        ) in selection_origins:
            path = (
                f"$.selections[{index}]"
                ".selection_origin"
            )
            asset_shortlist = shortlist_sets.get(
                asset_type,
                set(),
            )
            if origin == "python_shortlist":
                if symbol not in asset_shortlist:
                    errors.append(
                        _issue(
                            "PYTHON_SHORTLIST_ORIGIN_MISMATCH",
                            f"{symbol}未进入对应的{asset_type} Python榜",
                            path=path,
                        )
                    )
            elif origin == "codex_supplement":
                supplement_count += 1
                if symbol in asset_shortlist:
                    errors.append(
                        _issue(
                            "CODEX_SUPPLEMENT_ORIGIN_MISMATCH",
                            f"{symbol}已在对应的{asset_type} Python榜内",
                            path=path,
                        )
                    )
            else:
                errors.append(
                    _issue(
                        "INVALID_SELECTION_ORIGIN",
                        f"{symbol}缺少有效selection_origin",
                        path=path,
                    )
                )
        if payload.get("status") == "success":
            minimum = int(
                supplement_policy.get(
                    "minimum_when_web_available",
                    0,
                )
            )
            maximum = int(
                supplement_policy.get(
                    "maximum",
                    len(selections),
                )
            )
            if not (
                minimum
                <= supplement_count
                <= maximum
            ):
                errors.append(
                    _issue(
                        "CODEX_SUPPLEMENT_COUNT_OUT_OF_RANGE",
                        "联网成功时Codex补充候选数量不符合策略",
                        path="$.selections",
                    )
                )
        elif supplement_count:
            errors.append(
                _issue(
                    "LOCAL_ONLY_CODEX_SUPPLEMENT_FORBIDDEN",
                    "未完成联网研究时不得标记Codex补充候选",
                    path="$.selections",
                )
            )

    discoveries = payload.get(
        "external_discoveries",
        [],
    )
    discoveries = (
        discoveries
        if isinstance(discoveries, list)
        else []
    )
    discovery_policy = input_policy.get(
        "external_discovery",
        {},
    )
    discovery_policy = (
        discovery_policy
        if isinstance(
            discovery_policy,
            Mapping,
        )
        else {}
    )
    discovery_symbols: list[str] = []
    if discovery_policy:
        for index, discovery in enumerate(
            discoveries
        ):
            if not isinstance(
                discovery,
                Mapping,
            ):
                continue
            symbol = str(
                discovery.get("symbol", "")
            ).strip().upper()
            discovery_symbols.append(symbol)
            if symbol in set(selected_symbols):
                errors.append(
                    _issue(
                        "EXTERNAL_DISCOVERY_ALREADY_SELECTED",
                        f"{symbol}已在60只粗选中",
                        path=(
                            "$.external_discoveries"
                            f"[{index}].symbol"
                        ),
                    )
                )
            asset_type = discovery.get(
                "asset_type"
            )
            candidate_type = discovery.get(
                "candidate_type"
            )
            stock_types = {
                "satellite",
                "emerging_compounder",
                "contrarian",
                "turnaround",
                "special_situation",
            }
            etf_types = {
                "broad_or_factor_etf",
                "thematic_etf",
                "diversifier_etf",
            }
            expected_types = (
                stock_types
                if asset_type == "stock"
                else etf_types
                if asset_type == "etf"
                else set()
            )
            if candidate_type not in expected_types:
                errors.append(
                    _issue(
                        "DISCOVERY_TYPE_ASSET_MISMATCH",
                        f"{symbol}的候选类型与资产类型不匹配",
                        path=(
                            "$.external_discoveries"
                            f"[{index}].candidate_type"
                        ),
                    )
                )
        if len(discovery_symbols) != len(
            set(discovery_symbols)
        ):
            errors.append(
                _issue(
                    "DUPLICATE_EXTERNAL_DISCOVERY",
                    "external_discoveries包含重复symbol",
                    path="$.external_discoveries",
                )
            )
        if payload.get("status") == "success":
            minimum = int(
                discovery_policy.get(
                    "minimum_when_web_available",
                    0,
                )
            )
            maximum = int(
                discovery_policy.get(
                    "maximum",
                    len(discoveries),
                )
            )
            if not (
                minimum
                <= len(discoveries)
                <= maximum
            ):
                errors.append(
                    _issue(
                        "EXTERNAL_DISCOVERY_COUNT_OUT_OF_RANGE",
                        "联网成功时外部发现数量不符合策略",
                        path="$.external_discoveries",
                    )
                )
        elif discoveries:
            errors.append(
                _issue(
                    "LOCAL_ONLY_EXTERNAL_DISCOVERY_FORBIDDEN",
                    "未完成联网研究时不得输出外部发现",
                    path="$.external_discoveries",
                )
            )

    if len(set(selected_symbols)) != len(
        selected_symbols
    ):
        errors.append(
            _issue(
                "DUPLICATE_SELECTION_SYMBOL",
                "selections包含重复symbol",
                path="$.selections",
            )
        )
    if sorted(ranks) != list(range(1, 61)):
        errors.append(
            _issue(
                "INVALID_SELECTION_RANKS",
                "rank必须无重复覆盖1到60",
                path="$.selections",
            )
        )

    selected_set = set(selected_symbols)
    must_include = {
        str(value).strip().upper()
        for value in input_payload.get(
            "must_include",
            [],
        )
    }
    missing_required = sorted(
        must_include - selected_set
    )
    if missing_required:
        errors.append(
            _issue(
                "MUST_INCLUDE_MISSING",
                "缺少必须覆盖标的："
                + ",".join(missing_required),
                path="$.selections",
            )
        )
    exclusions = {
        str(value).strip().upper()
        for value in input_payload.get(
            "exclusions",
            [],
        )
    }
    selected_exclusions = sorted(
        exclusions & selected_set
    )
    if selected_exclusions:
        errors.append(
            _issue(
                "EXCLUDED_SYMBOL_SELECTED",
                "选择了排除标的："
                + ",".join(selected_exclusions),
                path="$.selections",
            )
        )

    for forbidden_path in _forbidden_paths(
        payload
    ):
        errors.append(
            _issue(
                "FORBIDDEN_OUTPUT_FIELD",
                "粗选输出包含禁止字段",
                path=forbidden_path,
            )
        )

    sources = payload.get(
        "source_references",
        [],
    )
    source_ids: list[str] = []
    if isinstance(sources, list):
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                continue
            source_id = str(
                source.get("id", "")
            ).strip()
            source_ids.append(source_id)
            if (
                source.get("source_type") == "web"
                and not str(
                    source.get("url", "")
                ).startswith(("https://", "http://"))
            ):
                errors.append(
                    _issue(
                        "INVALID_WEB_SOURCE_URL",
                        "web来源必须有http(s) URL",
                        path=(
                            "$.source_references"
                            f"[{index}].url"
                        ),
                    )
                )
    if len(set(source_ids)) != len(source_ids):
        errors.append(
            _issue(
                "DUPLICATE_SOURCE_ID",
                "source_references.id不能重复",
                path="$.source_references",
            )
        )
    source_id_set = set(source_ids)
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            continue
        references = selection.get(
            "source_references",
            [],
        )
        if not isinstance(references, list):
            continue
        unknown = sorted(
            {
                str(value)
                for value in references
            }
            - source_id_set
        )
        if unknown:
            errors.append(
                _issue(
                    "UNKNOWN_SOURCE_REFERENCE",
                    "选择项引用了未知来源："
                    + ",".join(unknown),
                    path=(
                        "$.selections"
                        f"[{index}].source_references"
                    ),
                )
            )
    if discovery_policy:
        web_source_ids = {
            str(source.get("id", "")).strip()
            for source in sources
            if isinstance(source, Mapping)
            and source.get("source_type") == "web"
        }
        for index, discovery in enumerate(
            discoveries
        ):
            if not isinstance(
                discovery,
                Mapping,
            ):
                continue
            references = discovery.get(
                "source_references",
                [],
            )
            reference_set = {
                str(value)
                for value in references
            } if isinstance(references, list) else set()
            unknown = sorted(
                reference_set - source_id_set
            )
            if unknown:
                errors.append(
                    _issue(
                        "UNKNOWN_SOURCE_REFERENCE",
                        "外部发现引用了未知来源："
                        + ",".join(unknown),
                        path=(
                            "$.external_discoveries"
                            f"[{index}].source_references"
                        ),
                    )
                )
            if (
                discovery_policy.get(
                    "require_primary_web_source"
                )
                is True
                and not (
                    reference_set
                    & web_source_ids
                )
            ):
                errors.append(
                    _issue(
                        "EXTERNAL_DISCOVERY_WEB_SOURCE_MISSING",
                        "外部发现必须引用可核验的web来源",
                        path=(
                            "$.external_discoveries"
                            f"[{index}].source_references"
                        ),
                    )
                )

    generated = _parse_datetime(
        payload.get("generated_at")
    )
    if generated is None:
        errors.append(
            _issue(
                "INVALID_GENERATED_AT",
                "generated_at不是可解析时间",
                path="$.generated_at",
            )
        )
    else:
        current = now or datetime.now(
            timezone.utc
        )
        if current.tzinfo is None:
            current = current.replace(
                tzinfo=timezone.utc
            )
        try:
            run_day = date.fromisoformat(
                str(input_payload["run_date"])
            )
        except (KeyError, ValueError):
            run_day = generated.date()
        if generated > current.astimezone(
            timezone.utc
        ) + timedelta(days=1):
            errors.append(
                _issue(
                    "GENERATED_AT_IN_FUTURE",
                    "generated_at不应显著晚于当前时间",
                    path="$.generated_at",
                )
            )
        if generated.date() < run_day - timedelta(
            days=1
        ):
            errors.append(
                _issue(
                    "GENERATED_AT_TOO_OLD",
                    "generated_at早于运行日期",
                    path="$.generated_at",
                )
            )

    status = payload.get("status")
    network = payload.get(
        "network_research",
        {},
    )
    output_warnings = payload.get(
        "warnings",
        [],
    )
    warning_text = " ".join(
        str(value).lower()
        for value in output_warnings
    )
    if isinstance(network, dict):
        warning_text += " " + " ".join(
            str(value).lower()
            for value in network.get(
                "warnings",
                [],
            )
        )
    if status == "success_local_only":
        if not any(
            token in warning_text
            for token in (
                "local",
                "network",
                "web",
                "unavailable",
                "网络",
                "本地",
            )
        ):
            errors.append(
                _issue(
                    "LOCAL_ONLY_WARNING_MISSING",
                    "本地降级成功必须明确记录网络限制",
                    path="$.warnings",
                )
            )
        if not isinstance(network, dict) or (
            network.get("web_access") is not False
            or network.get("status")
            not in {
                "unavailable",
                "not_requested",
            }
        ):
            errors.append(
                _issue(
                    "LOCAL_ONLY_NETWORK_STATUS_INCONSISTENT",
                    "success_local_only必须对应未完成的网络研究",
                    path="$.network_research",
                )
            )
    if (
        status == "success"
        and isinstance(network, dict)
        and (
            network.get("status") != "completed"
            or network.get("web_access")
            is not True
        )
    ):
        errors.append(
            _issue(
                "NETWORK_STATUS_INCONSISTENT",
                "success状态必须对应已完成的网络研究",
                path="$.network_research",
            )
        )

    held = {
        symbol
        for symbol, item in input_items.items()
        if (
            item.get("currently_held") is True
            or item.get("held") is True
        )
    }
    open_order = {
        symbol
        for symbol, item in input_items.items()
        if item.get("has_open_order") is True
    }
    omitted_held = sorted(held - selected_set)
    omitted_open = sorted(
        open_order - selected_set
    )
    if omitted_held:
        warnings.append(
            _issue(
                "HELD_SYMBOL_NOT_SELECTED",
                "持仓未进入60只候选："
                + ",".join(omitted_held),
                path="$.selections",
            )
        )
    if omitted_open:
        warnings.append(
            _issue(
                "OPEN_ORDER_SYMBOL_NOT_SELECTED",
                "挂单标的未进入60只候选："
                + ",".join(omitted_open),
                path="$.selections",
            )
        )

    schema_valid = not schema_errors
    business_errors = [
        error
        for error in errors
        if error["code"]
        != "SCHEMA_VALIDATION_FAILED"
    ]
    business_valid = not business_errors
    return CoarseValidationResult(
        valid=(
            schema_valid and business_valid
        ),
        schema_valid=schema_valid,
        business_valid=business_valid,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
