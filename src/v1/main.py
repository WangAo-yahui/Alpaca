from collections.abc import Callable
from pathlib import Path

from fetch_account import fetch_and_save_account
from fetch_assets import fetch_and_save_assets
from fetch_daily_bars import fetch_and_save_daily_bars
from fetch_intraday_bars import fetch_and_save_intraday_bars
from fetch_open_orders import fetch_and_save_open_orders
from fetch_positions import fetch_and_save_positions

from fetch_today_orders import fetch_and_save_today_orders
def run_snapshot_task(
    task_name: str,
    task_function: Callable[[], Path],
) -> bool:
    """
    运行账户类快照任务。

    成功返回 True，失败返回 False。
    """
    print()
    print("=" * 60)
    print(f"开始：{task_name}")
    print("=" * 60)

    try:
        output_path = task_function()

        print(f"{task_name}成功")
        print(f"保存位置：{output_path}")

        return True

    except Exception as error:
        print(f"{task_name}失败")
        print(f"错误信息：{error}")

        return False


def run_daily_bars_task() -> bool:
    """运行日线下载任务。"""
    print()
    print("=" * 60)
    print("开始：下载日线")
    print("=" * 60)

    try:
        successful_symbols, failed_symbols = (
            fetch_and_save_daily_bars()
        )

        print()
        print(f"日线成功数量：{len(successful_symbols)}")
        print(f"日线失败数量：{len(failed_symbols)}")

        if failed_symbols:
            print(
                "日线失败标的："
                + ", ".join(failed_symbols)
            )
            return False

        return True

    except Exception as error:
        print("日线下载任务失败")
        print(f"错误信息：{error}")

        return False


def run_intraday_bars_task() -> bool:
    """
    运行盘中5分钟K线下载任务。

    不在交易时段、周末或暂无数据不视为失败。
    """
    print()
    print("=" * 60)
    print("开始：下载盘中5分钟K线")
    print("=" * 60)

    try:
        (
            successful_symbols,
            no_data_symbols,
            failed_symbols,
        ) = fetch_and_save_intraday_bars()

        print()
        print(
            f"盘中成功数量：{len(successful_symbols)}"
        )
        print(
            f"盘中暂无数据数量：{len(no_data_symbols)}"
        )
        print(
            f"盘中失败数量：{len(failed_symbols)}"
        )

        if no_data_symbols:
            print(
                "当前暂无盘中数据："
                + ", ".join(no_data_symbols)
            )

        if failed_symbols:
            print(
                "盘中失败标的："
                + ", ".join(failed_symbols)
            )
            return False

        return True

    except Exception as error:
        print("盘中K线下载任务失败")
        print(f"错误信息：{error}")

        return False


def main() -> int:
    """
运行 v1 版本的全部数据采集任务。

依次获取：
- 账户信息
- 候选资产交易状态
- 当前持仓
- 未完成订单
- 当日订单和真实成交
- 最近日线
- 当日盘中5分钟K线

当前只读取和保存数据，不会提交、修改或取消订单。
"""
    print("Alpaca v1 数据采集开始")
    print("当前程序不会执行任何交易操作")

    task_results: dict[str, bool] = {}

    task_results["account"] = run_snapshot_task(
        task_name="读取账户信息",
        task_function=fetch_and_save_account,
    )

    task_results["assets"] = run_snapshot_task(
    task_name="读取资产交易状态",
    task_function=fetch_and_save_assets,
)

    task_results["positions"] = run_snapshot_task(
        task_name="读取当前持仓",
        task_function=fetch_and_save_positions,
    )

    task_results["open_orders"] = run_snapshot_task(
        task_name="读取未完成订单",
        task_function=fetch_and_save_open_orders,
    )

    task_results["today_orders"] = run_snapshot_task(
    task_name="读取当日订单和成交",
    task_function=fetch_and_save_today_orders,
    )


    task_results["daily_bars"] = (
        run_daily_bars_task()
    )

    task_results["intraday_bars"] = (
        run_intraday_bars_task()
    )

    successful_tasks = [
        name
        for name, succeeded in task_results.items()
        if succeeded
    ]

    failed_tasks = [
        name
        for name, succeeded in task_results.items()
        if not succeeded
    ]

    print()
    print("=" * 60)
    print("本次数据采集完成")
    print("=" * 60)
    print(f"成功任务数量：{len(successful_tasks)}")
    print(f"失败任务数量：{len(failed_tasks)}")

    if successful_tasks:
        print(
            "成功任务："
            + ", ".join(successful_tasks)
        )

    if failed_tasks:
        print(
            "失败任务："
            + ", ".join(failed_tasks)
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())