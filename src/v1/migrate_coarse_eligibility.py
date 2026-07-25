import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import (
    Draft202012Validator,
    FormatChecker,
)

from run_coarse_codex import (
    calculate_input_hash,
    copy_runtime_contracts,
    load_state,
    save_json_atomically,
    sha256_file,
)
from runtime_paths import (
    build_runtime_paths,
    find_latest_stage_workspace,
    get_project_root,
)
from validate_coarse_candidates import (
    validate_coarse_candidates,
)


SCRIPT_VERSION = (
    "2026-07-22-migrate-coarse-eligibility-v1"
)


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


def build_input_lookup(
    coarse_input: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造Codex研究范围索引。"""
    records = coarse_input.get(
        "codex_review_universe",
        [],
    )

    if not isinstance(records, list):
        raise ValueError(
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

        if symbol:
            lookup[symbol] = record

    return lookup


def validate_schema(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """在写入前执行新Schema校验。"""
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
            "$"
            if not error.absolute_path
            else "$."
            + ".".join(
                str(item)
                for item
                in error.absolute_path
            )
        )
        + "："
        + error.message
        for error in errors
    ]


def migrate_output(
    *,
    output: dict[str, Any],
    coarse_input: dict[str, Any],
) -> dict[str, Any]:
    """把旧资格字段转换为1.1结构。"""
    migrated = json.loads(
        json.dumps(output)
    )

    migrated["schema_version"] = "1.1"

    selected = migrated.get(
        "selected",
        [],
    )

    if not isinstance(selected, list):
        raise ValueError(
            "selected必须是数组"
        )

    input_lookup = build_input_lookup(
        coarse_input
    )

    for index, record in enumerate(selected):
        if not isinstance(record, dict):
            raise ValueError(
                f"selected[{index}]必须是对象"
            )

        symbol = normalize_symbol(
            record.get("symbol")
        )

        source_record = input_lookup.get(
            symbol
        )

        if source_record is None:
            raise ValueError(
                f"{symbol}不在Codex研究范围内"
            )

        source_status = source_record.get(
            "screen_status"
        )

        record.pop(
            "new_position_allowed",
            None,
        )

        record["research_eligible"] = True
        record[
            "screen_new_position_eligible"
        ] = (
            source_status == "eligible"
        )

    return migrated


def update_state(
    *,
    workspace: Path,
    output_path: Path,
) -> Path:
    """同步更新状态文件的哈希和迁移记录。"""
    project_root = get_project_root()
    run_date = workspace.parent.name

    paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    state = load_state(
        paths.decision_state
    )

    stages = state.setdefault(
        "stages",
        {},
    )

    stage = stages.setdefault(
        "coarse_selection",
        {},
    )

    stage["input_hash"] = (
        calculate_input_hash(
            workspace
        )
    )
    stage["output_hash"] = (
        sha256_file(output_path)
    )
    stage["selection_count"] = 60
    stage[
        "eligibility_schema_version"
    ] = "1.1"
    stage["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    stage["message"] = (
        "粗选结果已原子迁移到"
        "research_eligible和"
        "screen_new_position_eligible；"
        "候选选择本身未改变"
    )

    save_json_atomically(
        paths.decision_state,
        state,
    )

    return paths.decision_state


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    parser = argparse.ArgumentParser(
        description=(
            "把已验证粗选结果迁移到"
            "拆分后的资格字段"
        )
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "粗选工作区；默认使用纽约当天"
            "或最新工作区"
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

        # 先把项目中的新版Prompt和Schema
        # 原子刷新到工作区。
        copy_runtime_contracts(
            project_root=project_root,
            workspace=workspace,
        )

        output_path = (
            workspace
            / "output"
            / "coarse_candidates.json"
        )
        input_path = (
            workspace
            / "data"
            / "snapshots"
            / "coarse_universe_input.json"
        )
        schema_path = (
            workspace
            / "schemas"
            / "coarse_candidates.schema.json"
        )

        original = load_json_object(
            output_path
        )
        coarse_input = load_json_object(
            input_path
        )
        schema = load_json_object(
            schema_path
        )

        migrated = migrate_output(
            output=original,
            coarse_input=coarse_input,
        )

        schema_errors = validate_schema(
            payload=migrated,
            schema=schema,
        )

        if schema_errors:
            raise RuntimeError(
                "迁移结果未通过新Schema：\n- "
                + "\n- ".join(schema_errors)
            )

        save_json_atomically(
            output_path,
            migrated,
        )

        validation = (
            validate_coarse_candidates(
                workspace=workspace,
                max_age_hours=24,
            )
        )

        if not validation["valid"]:
            # 失败时恢复旧内容，避免丢失成功结果。
            save_json_atomically(
                output_path,
                original,
            )

            raise RuntimeError(
                "迁移后业务校验失败，已恢复原文件：\n- "
                + "\n- ".join(
                    validation["errors"]
                )
            )

        state_path = update_state(
            workspace=workspace,
            output_path=output_path,
        )

        print("粗选资格字段迁移完成")
        print(f"工作区：{workspace}")
        print(
            "Schema版本：1.1"
        )
        print(
            "候选数量："
            f"{validation['selection_count']}"
        )
        print(
            "校验结果：通过"
        )
        print(f"输出：{output_path}")
        print(f"状态：{state_path}")
        print(
            "候选选择未改变，"
            "未重新调用Codex"
        )

        return 0

    except Exception as error:
        print("粗选资格字段迁移失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
