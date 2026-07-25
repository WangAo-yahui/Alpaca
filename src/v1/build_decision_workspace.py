import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_project_root
from fetch_account import save_json_atomically


def load_json_file(file_path: Path) -> dict[str, Any]:
    """读取 JSON 文件，并确认顶层结构是对象。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"没有找到所需文件：{file_path}"
        )

    with file_path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    if not isinstance(content, dict):
        raise ValueError(
            f"JSON 文件顶层必须是对象：{file_path}"
        )

    return content


def copy_file_atomically(
    source_path: Path,
    destination_path: Path,
) -> None:
    """
    原子复制文件。

    先复制到临时文件，完成后再替换目标文件，
    避免中途中断留下不完整文件。
    """
    if not source_path.exists():
        raise FileNotFoundError(
            f"没有找到需要复制的文件：{source_path}"
        )

    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = destination_path.with_suffix(
        destination_path.suffix + ".tmp"
    )

    shutil.copy2(
        source_path,
        temporary_path,
    )

    temporary_path.replace(destination_path)


def rebuild_data_directory(
    workspace_root: Path,
) -> Path:
    """
    重新创建工作区的数据目录。

    只删除 decision_workspace/data，
    不删除 research 和 output。
    """
    data_directory = workspace_root / "data"

    if data_directory.exists():
        shutil.rmtree(data_directory)

    data_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return data_directory


def create_workspace_directories(
    workspace_root: Path,
) -> None:
    """创建 Codex 可以使用的工作区目录。"""
    directories = [
    workspace_root / "config",
    workspace_root / "schemas",
    workspace_root / "prompts",
    workspace_root / "research",
    workspace_root / "research" / "news",
    workspace_root / "research" / "companies",
    workspace_root / "research" / "macro",
    workspace_root / "reports",
    workspace_root / "reports" / "daily",
    workspace_root / "output",
    workspace_root / "output" / "archive",
    ]

    for directory in directories:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def archive_previous_order_plan(
    workspace_root: Path,
) -> Path | None:
    """
    归档上一轮订单计划。

    避免本轮 Codex 尚未生成新计划时，
    校验器误读上一轮的 order_plan.json。
    """
    order_plan_path = (
        workspace_root
        / "output"
        / "order_plan.json"
    )

    if not order_plan_path.exists():
        return None

    archive_directory = (
        workspace_root
        / "output"
        / "archive"
    )

    archive_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    archive_path = (
        archive_directory
        / f"{timestamp}_order_plan.json"
    )

    counter = 1

    while archive_path.exists():
        archive_path = (
            archive_directory
            / f"{timestamp}_{counter}_order_plan.json"
        )
        counter += 1

    order_plan_path.replace(archive_path)

    return archive_path


def get_selected_entries(
    candidate_input: dict[str, Any],
) -> list[dict[str, Any]]:
    """读取本轮提供给 Codex 的候选标的。"""
    selected_entries = candidate_input.get(
        "selected_for_codex",
        [],
    )

    if not isinstance(selected_entries, list):
        raise ValueError(
            "candidate_input.json 中的 "
            "selected_for_codex 必须是数组"
        )

    valid_entries: list[dict[str, Any]] = []

    seen_symbols: set[str] = set()

    for entry in selected_entries:
        if not isinstance(entry, dict):
            raise ValueError(
                "selected_for_codex 中的每个元素"
                "都必须是对象"
            )

        symbol = entry.get("symbol")

        if not isinstance(symbol, str):
            raise ValueError(
                "候选标的缺少有效的 symbol"
            )

        cleaned_symbol = symbol.strip().upper()

        if not cleaned_symbol:
            raise ValueError(
                "候选标的代码不能为空"
            )

        if cleaned_symbol in seen_symbols:
            raise ValueError(
                f"候选标的重复：{cleaned_symbol}"
            )

        copied_entry = copy.deepcopy(entry)
        copied_entry["symbol"] = cleaned_symbol

        valid_entries.append(copied_entry)
        seen_symbols.add(cleaned_symbol)

    if not valid_entries:
        raise ValueError(
            "本轮没有任何标的提供给 Codex"
        )

    return valid_entries


def resolve_project_file(
    project_root: Path,
    relative_path_value: Any,
) -> Path | None:
    """
    将候选文件中的相对路径转换为项目内的绝对路径。

    拒绝指向项目目录以外的路径。
    """
    if not isinstance(relative_path_value, str):
        return None

    if not relative_path_value.strip():
        return None

    relative_path = Path(
        relative_path_value.strip()
    )

    if relative_path.is_absolute():
        raise ValueError(
            f"原始数据路径不能是绝对路径："
            f"{relative_path}"
        )

    resolved_path = (
        project_root / relative_path
    ).resolve()

    resolved_project_root = (
        project_root.resolve()
    )

    try:
        resolved_path.relative_to(
            resolved_project_root
        )
    except ValueError as error:
        raise ValueError(
            f"原始数据路径超出项目目录："
            f"{relative_path}"
        ) from error

    return resolved_path


def copy_selected_market_files(
    project_root: Path,
    workspace_root: Path,
    selected_entries: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """
    复制本轮候选标的的日线和盘中数据。

    同时将候选文件中的 raw_files 路径改为
    decision_workspace 内部的相对路径。
    """
    copied_files: list[str] = []
    missing_files: list[str] = []

    for entry in selected_entries:
        symbol = entry["symbol"]

        market_summary = entry.get(
            "market_summary",
            {},
        )

        if not isinstance(market_summary, dict):
            market_summary = {}
            entry["market_summary"] = (
                market_summary
            )

        raw_files = market_summary.get(
            "raw_files",
            {},
        )

        if not isinstance(raw_files, dict):
            raw_files = {}
            market_summary["raw_files"] = (
                raw_files
            )

        daily_source = resolve_project_file(
            project_root=project_root,
            relative_path_value=raw_files.get(
                "daily"
            ),
        )

        daily_destination = (
            workspace_root
            / "data"
            / "raw_bars"
            / "daily"
            / f"{symbol}.json"
        )

        if (
            daily_source is not None
            and daily_source.exists()
        ):
            copy_file_atomically(
                source_path=daily_source,
                destination_path=daily_destination,
            )

            workspace_daily_path = (
                daily_destination.relative_to(
                    workspace_root
                )
            )

            raw_files["daily"] = str(
                workspace_daily_path
            )

            copied_files.append(
                str(workspace_daily_path)
            )
        else:
            raw_files["daily"] = None
            missing_files.append(
                f"{symbol}:daily"
            )

        intraday_source = resolve_project_file(
            project_root=project_root,
            relative_path_value=raw_files.get(
                "intraday"
            ),
        )

        if (
            intraday_source is not None
            and intraday_source.exists()
        ):
            market_date = (
                intraday_source.parent.name
            )

            intraday_destination = (
                workspace_root
                / "data"
                / "raw_bars"
                / "intraday"
                / market_date
                / f"{symbol}.json"
            )

            copy_file_atomically(
                source_path=intraday_source,
                destination_path=(
                    intraday_destination
                ),
            )

            workspace_intraday_path = (
                intraday_destination.relative_to(
                    workspace_root
                )
            )

            raw_files["intraday"] = str(
                workspace_intraday_path
            )

            copied_files.append(
                str(workspace_intraday_path)
            )
        else:
            raw_files["intraday"] = None

    return copied_files, missing_files


def build_compact_candidate_input(
    candidate_input: dict[str, Any],
    selected_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构建 Codex 实际读取的精简候选文件。

    不把所有普通候选的完整数据重复复制进去，
    只保留：
    - 账户和持仓
    - 挂单
    - 本轮选中标的
    - 风险隔离和硬排除摘要
    """
    quarantined = candidate_input.get(
        "quarantined",
        [],
    )

    excluded = candidate_input.get(
        "excluded",
        [],
    )

    quarantined_summary: list[
        dict[str, Any]
    ] = []

    if isinstance(quarantined, list):
        for item in quarantined:
            if not isinstance(item, dict):
                continue

            quarantined_summary.append(
                {
                    "symbol": item.get("symbol"),
                    "forced_include": item.get(
                        "forced_include",
                        False,
                    ),
                    "risk_filter_reasons": (
                        item.get(
                            "risk_filter_reasons",
                            [],
                        )
                    ),
                }
            )

    excluded_summary: list[
        dict[str, Any]
    ] = []

    if isinstance(excluded, list):
        for item in excluded:
            if not isinstance(item, dict):
                continue

            excluded_summary.append(
                {
                    "symbol": item.get("symbol"),
                    "forced_include": item.get(
                        "forced_include",
                        False,
                    ),
                    "hard_filter_reasons": (
                        item.get(
                            "hard_filter_reasons",
                            [],
                        )
                    ),
                }
            )

    return {
        "schema_version": candidate_input.get(
            "schema_version",
            "1.0",
        ),
        "source_generated_at": (
            candidate_input.get("generated_at")
        ),
        "workspace_generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": candidate_input.get(
            "status",
            "unknown",
        ),
        "purpose": (
            "供 Codex 在隔离工作区中进行新闻研究、"
            "仓位判断和订单计划设计。"
        ),
        "run_context": candidate_input.get(
            "run_context",
            {},
        ),
        "account": candidate_input.get(
            "account",
            {},
        ),
        "portfolio": candidate_input.get(
            "portfolio",
            {},
        ),
        "open_orders": candidate_input.get(
            "open_orders",
            {},
        ),
        "selection_summary": (
            candidate_input.get(
                "selection_summary",
                {},
            )
        ),
        "selected_for_codex": selected_entries,
        "risk_overview": {
            "quarantined": (
                quarantined_summary
            ),
            "excluded": excluded_summary,
        },
        "notes": candidate_input.get(
            "notes",
            [],
        ),
    }


def find_latest_report(
    report_directory: Path,
) -> Path | None:
    """找到指定目录中名称最新的 Markdown 报告。"""
    if not report_directory.exists():
        return None

    report_files = sorted(
        [
            path
            for path in report_directory.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".md"
        ],
        key=lambda path: path.name,
        reverse=True,
    )

    if not report_files:
        return None

    return report_files[0]

def copy_today_orders(
    project_root: Path,
    workspace_root: Path,
) -> tuple[str | None, str | None]:
    """
    将当日订单和真实成交快照复制到决策工作区。

    文件不存在时不视为工作区构建失败，
    但会返回警告，Codex不得编造当日交易。
    """
    source_path = (
        project_root
        / "data"
        / "snapshots"
        / "today_orders.json"
    )

    destination_path = (
        workspace_root
        / "data"
        / "today_orders.json"
    )

    if not source_path.exists():
        return (
            None,
            "缺少 data/snapshots/today_orders.json，"
            "无法完整生成当日交易简报",
        )

    try:
        copy_file_atomically(
            source_path=source_path,
            destination_path=destination_path,
        )

    except Exception as error:
        return (
            None,
            "复制 today_orders.json 失败："
            f"{error}",
        )

    return (
        str(
            destination_path.relative_to(
                workspace_root
            )
        ),
        None,
    )


def copy_latest_reports(
    project_root: Path,
    workspace_root: Path,
) -> list[str]:
    """复制最新日报和周报到工作区。"""
    copied_reports: list[str] = []

    report_sources = {
        "daily": (
            project_root
            / "reports"
            / "daily"
        ),
        "weekly": (
            project_root
            / "reports"
            / "weekly"
        ),
    }

    for report_type, source_directory in (
        report_sources.items()
    ):
        latest_report = find_latest_report(
            source_directory
        )

        if latest_report is None:
            continue

        destination_path = (
            workspace_root
            / "data"
            / "reports"
            / report_type
            / latest_report.name
        )

        copy_file_atomically(
            source_path=latest_report,
            destination_path=destination_path,
        )

        copied_reports.append(
            str(
                destination_path.relative_to(
                    workspace_root
                )
            )
        )

    return copied_reports


def copy_workspace_rules(
    project_root: Path,
    workspace_root: Path,
) -> list[str]:
    """
    复制 Codex 所需的规则、提示词和 Schema。

    这些文件是副本，Codex修改工作区副本时，
    不会直接修改项目中的正式配置。
    """
    file_pairs = [
        (
            project_root
            / "config"
            / "order_policy.json",
            workspace_root
            / "config"
            / "order_policy.json",
        ),
        (
            project_root
            / "schemas"
            / "order_plan.schema.json",
            workspace_root
            / "schemas"
            / "order_plan.schema.json",
        ),
        (
            project_root
            / "prompts"
            / "position_decision.md",
            workspace_root
            / "prompts"
            / "position_decision.md",
        ),
    ]

    copied_files: list[str] = []

    for source_path, destination_path in file_pairs:
        copy_file_atomically(
            source_path=source_path,
            destination_path=destination_path,
        )

        copied_files.append(
            str(
                destination_path.relative_to(
                    workspace_root
                )
            )
        )

    return copied_files


def build_decision_workspace() -> Path:
    """构建供 Codex 使用的隔离决策工作区。"""
    project_root = get_project_root()

    workspace_root = (
        project_root
        / "decision_workspace"
    )

    candidate_input_path = (
        project_root
        / "data"
        / "snapshots"
        / "candidate_input.json"
    )

    candidate_input = load_json_file(
        candidate_input_path
    )

    create_workspace_directories(
        workspace_root
    )

    archived_plan = archive_previous_order_plan(
        workspace_root
    )

    rebuild_data_directory(
        workspace_root
    )

    selected_entries = get_selected_entries(
        candidate_input
    )

    (
        copied_market_files,
        missing_market_files,
    ) = copy_selected_market_files(
        project_root=project_root,
        workspace_root=workspace_root,
        selected_entries=selected_entries,
    )

    compact_candidate_input = (
        build_compact_candidate_input(
            candidate_input=candidate_input,
            selected_entries=selected_entries,
        )
    )

    candidate_destination = (
        workspace_root
        / "data"
        / "candidate_input.json"
    )

    save_json_atomically(
        candidate_destination,
        compact_candidate_input,
    )





    (
    copied_today_orders,
    today_orders_warning,
) = copy_today_orders(
    project_root=project_root,
    workspace_root=workspace_root,
)

    copied_reports = copy_latest_reports(
        project_root=project_root,
        workspace_root=workspace_root,
    )

    copied_rule_files = copy_workspace_rules(
        project_root=project_root,
        workspace_root=workspace_root,
    )




    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": (
            "success"
            if not missing_market_files
            else "partial"
        ),
        "workspace_root": str(
            workspace_root
        ),
        "selected_symbol_count": len(
            selected_entries
        ),
        "selected_symbols": [
            entry["symbol"]
            for entry in selected_entries
        ],
        "copied_market_file_count": len(
            copied_market_files
        ),
        "copied_market_files": (
            copied_market_files
        ),
        "missing_market_files": (
            missing_market_files
        ),
        "copied_today_orders": copied_today_orders,
        "today_orders_warning": today_orders_warning,
        "copied_reports": copied_reports,
        "copied_rule_files": copied_rule_files,
        "archived_previous_order_plan": (
            str(
                archived_plan.relative_to(
                    workspace_root
                )
            )
            if archived_plan is not None
            else None
        ),
        "preserved_directories": [
        "research/",
        "reports/daily/",
        "output/archive/",
    ],
        "security_boundary": {
            "contains_env_file": False,
            "contains_api_credentials": False,
            "contains_order_execution_code": False,
            "contains_only_copied_decision_data": True,
        },
    }

    manifest_path = (
        workspace_root
        / "data"
        / "workspace_manifest.json"
    )

    save_json_atomically(
        manifest_path,
        manifest,
    )

    return workspace_root


def main() -> int:
    """单独运行本文件时构建决策工作区。"""
    try:
        workspace_root = (
            build_decision_workspace()
        )

        manifest_path = (
            workspace_root
            / "data"
            / "workspace_manifest.json"
        )

        manifest = load_json_file(
            manifest_path
        )

        print("Codex 决策工作区构建成功")
        print(f"工作区位置：{workspace_root}")
        print(
            "候选标的数量："
            f"{manifest.get('selected_symbol_count', 0)}"
        )
        print(
            "复制行情文件数量："
            f"{manifest.get('copied_market_file_count', 0)}"
        )

        missing_files = manifest.get(
            "missing_market_files",
            [],
        )

        if missing_files:
            print(
                "警告：部分候选标的缺少日线文件"
            )
            print(
                "缺少文件："
                + ", ".join(missing_files)
            )

        archived_plan = manifest.get(
            "archived_previous_order_plan"
        )

        if archived_plan:
            print(
                "上一轮订单计划已归档："
                f"{archived_plan}"
            )

        print(
            "research 和 output/archive "
            "目录中的内容已保留"
        )
        print(
            "工作区不包含 .env、API密钥"
            "或订单执行代码"
        )

        return 0

    except Exception as error:
        print("Codex 决策工作区构建失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())