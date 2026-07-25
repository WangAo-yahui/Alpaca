import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_project_root() -> Path:
    """返回项目根目录。"""
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = get_project_root()
SOURCE_DIRECTORY = PROJECT_ROOT / "src" / "v1"

CANDIDATE_INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "snapshots"
    / "candidate_input.json"
)

PIPELINE_REPORT_PATH = (
    PROJECT_ROOT
    / "output"
    / "pipeline"
    / "latest.json"
)


def save_json_atomically(
    output_path: Path,
    payload: dict[str, Any],
) -> None:
    """原子写入JSON，避免中途退出留下半个文件。"""
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = output_path.with_suffix(
        output_path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temporary_path.replace(output_path)


def read_candidate_symbols() -> list[str]:
    """读取当前 selected_for_codex 标的列表。"""
    if not CANDIDATE_INPUT_PATH.exists():
        return []

    try:
        with CANDIDATE_INPUT_PATH.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []

    candidates = payload.get(
        "selected_for_codex",
        [],
    )

    if not isinstance(candidates, list):
        return []

    symbols: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol", "")
        ).strip().upper()

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)
        symbols.append(symbol)

    return symbols


def run_script(
    script_name: str,
    display_name: str,
) -> dict[str, Any]:
    """
    使用当前虚拟环境中的Python运行单个脚本。

    子进程输出会直接显示在终端。
    非零退出码会被视为失败。
    """
    script_path = (
        SOURCE_DIRECTORY
        / script_name
    )

    if not script_path.exists():
        raise FileNotFoundError(
            f"没有找到脚本：{script_path}"
        )

    print()
    print("=" * 72)
    print(f"开始：{display_name}")
    print(f"脚本：{script_path.relative_to(PROJECT_ROOT)}")
    print("=" * 72)

    started_at = datetime.now(
        timezone.utc
    )

    started_monotonic = time.monotonic()

    completed_process = subprocess.run(
        [
            sys.executable,
            "-u",
            str(script_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )

    duration_seconds = (
        time.monotonic()
        - started_monotonic
    )

    finished_at = datetime.now(
        timezone.utc
    )

    succeeded = (
        completed_process.returncode == 0
    )

    print()
    print(
        f"{display_name}："
        f"{'成功' if succeeded else '失败'}"
    )
    print(
        "耗时："
        f"{duration_seconds:.2f} 秒"
    )

    return {
        "script": str(
            script_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "display_name": display_name,
        "started_at": (
            started_at.isoformat()
        ),
        "finished_at": (
            finished_at.isoformat()
        ),
        "duration_seconds": round(
            duration_seconds,
            3,
        ),
        "return_code": (
            completed_process.returncode
        ),
        "succeeded": succeeded,
    }


def execute_step(
    results: list[dict[str, Any]],
    script_name: str,
    display_name: str,
) -> None:
    """运行必须成功的步骤；失败时立即终止流水线。"""
    result = run_script(
        script_name=script_name,
        display_name=display_name,
    )

    results.append(result)

    if not result["succeeded"]:
        raise RuntimeError(
            f"步骤失败：{display_name}"
        )


def build_pipeline_report(
    run_id: str,
    started_at: datetime,
    results: list[dict[str, Any]],
    status: str,
    error: str | None,
    candidate_passes: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建流水线运行报告。"""
    finished_at = datetime.now(
        timezone.utc
    )

    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "project_root": str(PROJECT_ROOT),
        "python_executable": sys.executable,
        "step_count": len(results),
        "successful_step_count": sum(
            1
            for item in results
            if item.get("succeeded")
        ),
        "failed_step_count": sum(
            1
            for item in results
            if not item.get("succeeded")
        ),
        "candidate_passes": candidate_passes,
        "steps": results,
        "error": error,
        "outputs": {
            "decision_input": (
                "data/snapshots/decision_input.json"
            ),
            "candidate_input": (
                "data/snapshots/candidate_input.json"
            ),
            "decision_workspace": (
                "decision_workspace/"
            ),
        },
    }


def run_pipeline(
    max_candidate_refreshes: int,
) -> int:
    """
    运行 WA Trader v1 数据与决策工作区流水线。

    顺序：
    1. 更新账户、资产、持仓、订单和当日成交。
    2. 更新全部标的日线。
    3. 生成不依赖最新盘中数据的第一次候选池。
    4. 仅为候选池下载盘中数据。
    5. 重建快照并再次筛选。
    6. 候选发生变化时，最多补抓指定轮数盘中数据。
    7. 构建隔离决策工作区。

    本程序不会调用Codex，也不会提交、修改或取消订单。
    """
    started_at = datetime.now(
        timezone.utc
    )

    run_id = started_at.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    results: list[dict[str, Any]] = []
    candidate_passes: list[
        dict[str, Any]
    ] = []

    status = "failed"
    error_message: str | None = None

    print("WA Trader v1 流水线开始")
    print(f"运行编号：{run_id}")
    print(f"Python：{sys.executable}")
    print("本流程不会执行任何交易操作")

    try:
        base_steps = [
            (
                "fetch_account.py",
                "读取账户信息",
            ),
            (
                "fetch_assets.py",
                "读取资产交易状态",
            ),
            (
                "fetch_positions.py",
                "读取当前持仓",
            ),
            (
                "fetch_open_orders.py",
                "读取未完成订单",
            ),
            (
                "fetch_today_orders.py",
                "读取当日订单和成交",
            ),
            (
                "fetch_daily_bars.py",
                "更新全部标的日线",
            ),
            (
                "build_snapshot.py",
                "生成第一次决策快照",
            ),
            (
                "filter_candidates.py",
                "生成第一次候选池",
            ),
        ]

        for script_name, display_name in (
            base_steps
        ):
            execute_step(
                results=results,
                script_name=script_name,
                display_name=display_name,
            )

        previous_candidates = (
            read_candidate_symbols()
        )

        candidate_passes.append(
            {
                "pass": 1,
                "candidate_count": len(
                    previous_candidates
                ),
                "symbols": previous_candidates,
                "added": previous_candidates,
                "removed": [],
            }
        )

        print()
        print(
            "第一次候选池数量："
            f"{len(previous_candidates)}"
        )

        if not previous_candidates:
            raise RuntimeError(
                "第一次候选池为空，"
                "停止下载盘中数据"
            )

        refresh_number = 0

        while True:
            refresh_number += 1

            execute_step(
                results=results,
                script_name=(
                    "fetch_intraday_bars.py"
                ),
                display_name=(
                    "下载候选标的盘中数据"
                    f"（第{refresh_number}轮）"
                ),
            )

            execute_step(
                results=results,
                script_name="build_snapshot.py",
                display_name=(
                    "使用最新盘中数据重建快照"
                    f"（第{refresh_number}轮）"
                ),
            )

            execute_step(
                results=results,
                script_name=(
                    "filter_candidates.py"
                ),
                display_name=(
                    "使用最新盘中数据刷新候选池"
                    f"（第{refresh_number + 1}次筛选）"
                ),
            )

            current_candidates = (
                read_candidate_symbols()
            )

            previous_set = set(
                previous_candidates
            )
            current_set = set(
                current_candidates
            )

            added = sorted(
                current_set - previous_set
            )
            removed = sorted(
                previous_set - current_set
            )

            candidate_passes.append(
                {
                    "pass": (
                        refresh_number + 1
                    ),
                    "candidate_count": len(
                        current_candidates
                    ),
                    "symbols": (
                        current_candidates
                    ),
                    "added": added,
                    "removed": removed,
                }
            )

            print()
            print(
                "候选池刷新结果："
            )
            print(
                "当前数量："
                f"{len(current_candidates)}"
            )
            print(
                "新增："
                + (
                    ", ".join(added)
                    if added
                    else "无"
                )
            )
            print(
                "移出："
                + (
                    ", ".join(removed)
                    if removed
                    else "无"
                )
            )

            if not added and not removed:
                print(
                    "候选池已经稳定"
                )
                break

            if (
                refresh_number
                >= max_candidate_refreshes
            ):
                print(
                    "候选池仍有变化，"
                    "但已达到盘中补抓轮数上限；"
                    "将使用最新候选池继续构建工作区"
                )
                break

            previous_candidates = (
                current_candidates
            )

        execute_step(
            results=results,
            script_name=(
                "build_decision_workspace.py"
            ),
            display_name="构建隔离决策工作区",
        )

        status = "success"

        print()
        print("=" * 72)
        print("WA Trader v1 流水线完成")
        print("=" * 72)
        print(
            "最终候选数量："
            f"{len(read_candidate_symbols())}"
        )
        print(
            "决策工作区："
            "decision_workspace/"
        )
        print(
            "下一步：从decision_workspace"
            "启动Codex进行研究和生成计划"
        )

        return_code = 0

    except Exception as error:
        error_message = str(error)

        print()
        print("=" * 72)
        print("WA Trader v1 流水线失败")
        print("=" * 72)
        print(f"错误信息：{error_message}")

        return_code = 1

    finally:
        report = build_pipeline_report(
            run_id=run_id,
            started_at=started_at,
            results=results,
            status=status,
            error=error_message,
            candidate_passes=(
                candidate_passes
            ),
        )

        save_json_atomically(
            PIPELINE_REPORT_PATH,
            report,
        )

        print(
            "流水线报告："
            f"{PIPELINE_REPORT_PATH}"
        )

    return return_code


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description=(
            "运行WA Trader v1完整数据与"
            "决策工作区流水线"
        )
    )

    parser.add_argument(
        "--max-candidate-refreshes",
        type=int,
        default=2,
        help=(
            "候选池变化时，盘中数据最多补抓轮数。"
            "默认2轮。"
        ),
    )

    arguments = parser.parse_args()

    if arguments.max_candidate_refreshes < 1:
        print(
            "--max-candidate-refreshes "
            "必须至少为1"
        )
        return 2

    return run_pipeline(
        max_candidate_refreshes=(
            arguments
            .max_candidate_refreshes
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
