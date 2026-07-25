import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

from runtime_paths import (
    find_latest_stage_workspace,
    get_project_root,
)



SCRIPT_VERSION = "2026-07-22-canonical-paths-soft-balance-v3"

DEFAULT_MAX_OUTPUT_AGE_HOURS = 24


def load_json_object(
    path: Path,
) -> dict[str, Any]:
    """读取JSON对象。"""
    if not path.exists():
        raise FileNotFoundError(
            f"缺少文件：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON顶层必须是对象：{path}"
        )

    return payload


def normalize_symbol(
    value: Any,
) -> str:
    """标准化股票代码。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def parse_datetime(
    value: Any,
) -> datetime | None:
    """解析ISO 8601时间。"""
    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def format_schema_path(
    error: Any,
) -> str:
    """格式化JSON Schema错误路径。"""
    parts = [
        str(item)
        for item in error.absolute_path
    ]

    return (
        "$"
        if not parts
        else "$." + ".".join(parts)
    )


def validate_schema(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """执行Draft 2020-12 Schema校验。"""
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    return [
        (
            f"{format_schema_path(error)}："
            f"{error.message}"
        )
        for error in errors
    ]


def build_input_lookup(
    coarse_input: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """建立Codex允许研究标的的索引。"""
    records = coarse_input.get(
        "codex_review_universe",
        [],
    )

    if not isinstance(records, list):
        raise ValueError(
            "coarse_universe_input.json中的"
            "codex_review_universe必须是数组"
        )

    lookup: dict[
        str,
        dict[str, Any],
    ] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

        lookup[symbol] = record

    return lookup


def get_required_symbols(
    coarse_input: dict[str, Any],
) -> set[str]:
    """取得所有必须覆盖的标的。"""
    values = coarse_input.get(
        "required_symbols",
        [],
    )

    if not isinstance(values, list):
        raise ValueError(
            "required_symbols必须是数组"
        )

    return {
        symbol
        for symbol in (
            normalize_symbol(value)
            for value in values
        )
        if symbol
    }


def validate_selected_membership(
    output: dict[str, Any],
    input_lookup: dict[
        str,
        dict[str, Any],
    ],
    required_symbols: set[str],
) -> tuple[list[str], list[str]]:
    """
    校验60只候选的来源、重复、强制标的和开仓权限。

    返回：
    - errors
    - warnings
    """
    errors: list[str] = []
    warnings: list[str] = []

    selected = output.get(
        "selected",
        [],
    )

    if not isinstance(selected, list):
        return (
            ["selected必须是数组"],
            warnings,
        )

    symbols: list[str] = []

    for index, record in enumerate(
        selected
    ):
        if not isinstance(record, dict):
            errors.append(
                f"selected[{index}]必须是对象"
            )
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            errors.append(
                f"selected[{index}]缺少有效symbol"
            )
            continue

        symbols.append(symbol)

        source_record = input_lookup.get(
            symbol
        )

        if source_record is None:
            errors.append(
                f"{symbol}不属于Codex允许研究范围"
            )
            continue

        source_status = source_record.get(
            "screen_status"
        )

        source_forced = bool(
            source_record.get(
                "forced_include",
                False,
            )
        )

        output_forced = bool(
            record.get(
                "forced_include",
                False,
            )
        )

        new_position_allowed = bool(
            record.get(
                "new_position_allowed",
                False,
            )
        )

        if output_forced != source_forced:
            errors.append(
                f"{symbol}的forced_include与"
                "Python输入不一致："
                f"输入={source_forced}，"
                f"输出={output_forced}"
            )

        if (
            source_status != "eligible"
            and new_position_allowed
        ):
            errors.append(
                f"{symbol}状态为{source_status}，"
                "不得设置new_position_allowed=true"
            )

        if (
            source_status == "eligible"
            and not new_position_allowed
            and not source_forced
        ):
            warnings.append(
                f"{symbol}属于eligible但被设置为"
                "new_position_allowed=false"
            )

        source_references = record.get(
            "source_references",
            [],
        )

        if isinstance(
            source_references,
            list,
        ):
            normalized_references = [
                value
                for value in source_references
                if isinstance(value, str)
            ]

            if len(
                normalized_references
            ) != len(
                set(normalized_references)
            ):
                errors.append(
                    f"{symbol}的source_references"
                    "存在重复值"
                )

    duplicate_symbols = sorted(
        {
            symbol
            for symbol in symbols
            if symbols.count(symbol) > 1
        }
    )

    if duplicate_symbols:
        errors.append(
            "selected存在重复标的："
            + ", ".join(
                duplicate_symbols
            )
        )

    selected_set = set(symbols)

    missing_required = sorted(
        required_symbols
        - selected_set
    )

    if missing_required:
        errors.append(
            "遗漏required_symbols："
            + ", ".join(
                missing_required
            )
        )

    claimed_covered = output.get(
        "required_symbols_covered"
    )

    actual_covered = not missing_required

    if claimed_covered is not actual_covered:
        errors.append(
            "required_symbols_covered与"
            "实际覆盖结果不一致"
        )

    if len(symbols) != 60:
        errors.append(
            f"有效候选代码数量不是60："
            f"{len(symbols)}"
        )

    if len(selected_set) != 60:
        errors.append(
            f"去重后候选数量不是60："
            f"{len(selected_set)}"
        )

    return errors, warnings


def validate_research_files(
    workspace: Path,
    output: dict[str, Any],
) -> list[str]:
    """校验所有研究文件真实存在且位于工作区内。"""
    errors: list[str] = []

    values = output.get(
        "research_files",
        [],
    )

    if not isinstance(values, list):
        return [
            "research_files必须是数组"
        ]

    normalized_values = [
        value
        for value in values
        if isinstance(value, str)
    ]

    if len(normalized_values) != len(
        set(normalized_values)
    ):
        errors.append(
            "research_files存在重复路径"
        )

    workspace_resolved = workspace.resolve()

    for value in values:
        if not isinstance(value, str):
            errors.append(
                "research_files中存在非字符串"
            )
            continue

        relative_path = Path(value)

        if relative_path.is_absolute():
            errors.append(
                f"研究文件必须使用工作区相对路径："
                f"{value}"
            )
            continue

        resolved = (
            workspace
            / relative_path
        ).resolve()

        try:
            resolved.relative_to(
                workspace_resolved
            )
        except ValueError:
            errors.append(
                f"研究文件越出工作区：{value}"
            )
            continue

        if not resolved.exists():
            errors.append(
                f"研究文件不存在：{value}"
            )
            continue

        if not resolved.is_file():
            errors.append(
                f"研究路径不是文件：{value}"
            )

    return errors


def validate_output_freshness(
    output: dict[str, Any],
    max_age_hours: float,
) -> list[str]:
    """校验粗选结果是否过期或来自未来。"""
    errors: list[str] = []

    generated_at = parse_datetime(
        output.get("generated_at")
    )

    if generated_at is None:
        return [
            "generated_at不是有效ISO时间"
        ]

    now_utc = datetime.now(
        timezone.utc
    )

    generated_utc = generated_at.astimezone(
        timezone.utc
    )

    if generated_utc > (
        now_utc + timedelta(minutes=5)
    ):
        errors.append(
            "generated_at明显晚于当前时间"
        )

    age = now_utc - generated_utc

    if age > timedelta(
        hours=max_age_hours
    ):
        errors.append(
            "粗选结果已过期："
            f"{age.total_seconds() / 3600:.2f}小时"
        )

    return errors


def validate_coarse_candidates(
    workspace: Path,
    max_age_hours: float,
) -> dict[str, Any]:
    """执行第一次Codex粗选结果的完整校验。"""
    schema_path = (
        workspace
        / "schemas"
        / "coarse_candidates.schema.json"
    )

    coarse_input_path = (
        workspace
        / "data"
        / "snapshots"
        / "coarse_universe_input.json"
    )

    output_path = (
        workspace
        / "output"
        / "coarse_candidates.json"
    )

    schema = load_json_object(
        schema_path
    )

    coarse_input = load_json_object(
        coarse_input_path
    )

    output = load_json_object(
        output_path
    )

    errors = validate_schema(
        payload=output,
        schema=schema,
    )

    warnings: list[str] = []

    input_lookup = build_input_lookup(
        coarse_input
    )

    required_symbols = (
        get_required_symbols(
            coarse_input
        )
    )

    (
        membership_errors,
        membership_warnings,
    ) = validate_selected_membership(
        output=output,
        input_lookup=input_lookup,
        required_symbols=required_symbols,
    )

    errors.extend(membership_errors)
    warnings.extend(membership_warnings)

    errors.extend(
        validate_research_files(
            workspace=workspace,
            output=output,
        )
    )

    errors.extend(
        validate_output_freshness(
            output=output,
            max_age_hours=max_age_hours,
        )
    )

    selected = output.get(
        "selected",
        [],
    )

    symbols = [
        normalize_symbol(
            item.get("symbol")
        )
        for item in selected
        if isinstance(item, dict)
        and normalize_symbol(
            item.get("symbol")
        )
    ]

    return {
        "validated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "workspace": str(workspace),
        "output_path": str(output_path),
        "valid": not errors,
        "selection_count": len(symbols),
        "unique_selection_count": len(
            set(symbols)
        ),
        "required_symbol_count": len(
            required_symbols
        ),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    """命令行入口。"""
    print(f"脚本版本：{SCRIPT_VERSION}")
    parser = argparse.ArgumentParser(
        description=(
            "校验WA Trader v1第一次Codex"
            "生成的60只粗选候选"
        )
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "粗选工作区路径；默认使用纽约当天"
            "或最新的coarse_workspace"
        ),
    )

    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=(
            DEFAULT_MAX_OUTPUT_AGE_HOURS
        ),
        help=(
            "允许粗选结果存在的最长小时数，"
            "默认24小时"
        ),
    )

    arguments = parser.parse_args()

    try:
        project_root = get_project_root()

        workspace = (
            arguments.workspace
            if arguments.workspace
            else find_latest_stage_workspace(
                "coarse_selection",
                project_root=project_root,
            )
        )

        if not workspace.is_absolute():
            workspace = (
                project_root / workspace
            )

        workspace = workspace.resolve()

        result = validate_coarse_candidates(
            workspace=workspace,
            max_age_hours=(
                arguments.max_age_hours
            ),
        )

        print("粗选候选校验完成")
        print(f"工作区：{workspace}")
        print(
            "候选数量："
            f"{result['selection_count']}"
        )
        print(
            "去重数量："
            f"{result['unique_selection_count']}"
        )
        print(
            "必须覆盖标的："
            f"{result['required_symbol_count']}"
        )
        print(
            "校验结果："
            + (
                "通过"
                if result["valid"]
                else "不通过"
            )
        )

        if result["errors"]:
            print()
            print("错误：")

            for error in result["errors"]:
                print(f"- {error}")

        if result["warnings"]:
            print()
            print("警告：")

            for warning in result[
                "warnings"
            ]:
                print(f"- {warning}")

        return (
            0
            if result["valid"]
            else 1
        )

    except Exception as error:
        print("粗选候选校验失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())