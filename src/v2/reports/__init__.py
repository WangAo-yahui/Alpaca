"""Stage G 可审计日报包。

作用：导出首轮详细报告和同日后续增量更新的稳定入口。
重要性：聊天输出不可作为交易审计记录，日报必须与 cycle 产物同步保存在本地。
"""

from v2.reports.daily_report import update_daily_report

__all__ = ["update_daily_report"]
