"""计算净外部现金流校正后的账户日度时间加权收益。

作用：组合历史提供每日权益，账户活动提供入金、出金和转入转出；本模块只持久化
聚合现金流和收益，不保存活动ID、描述或账户标识。重要性：券商原始收益可能遗漏
加密资产转入，不能把净入金误报为投资收益。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from alpaca.trading.requests import GetPortfolioHistoryRequest

from v2.data.alpaca_client import AlpacaClients, call_api
from v2.runtime import utc_now_iso


NEW_YORK = ZoneInfo("America/New_York")
ZERO = Decimal("0")
ONE = Decimal("1")

CASH_DEPOSIT_TYPES = {"CSD"}
CASH_WITHDRAWAL_TYPES = {"CSW"}
TRANSFER_TYPES = {"ACATC", "JNLC", "OCT", "TRANS"}


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(NEW_YORK)


def _activity_datetime(activity: Mapping[str, Any]) -> datetime | None:
    transaction_time = activity.get("transaction_time")
    if transaction_time:
        return _parse_datetime(transaction_time)
    for field in ("entry_date", "date"):
        raw = activity.get(field)
        if isinstance(raw, str):
            try:
                day = date.fromisoformat(raw.strip()[:10])
            except ValueError:
                continue
            return datetime.combine(day, time.min, tzinfo=NEW_YORK)
    return None


def _history_date(timestamp: object) -> date | None:
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            return datetime.fromtimestamp(
                timestamp, tz=timezone.utc
            ).astimezone(NEW_YORK).date()
        except (OverflowError, OSError, ValueError):
            return None
    parsed = _parse_datetime(timestamp)
    return parsed.date() if parsed is not None else None


def _activity_amount(activity: Mapping[str, Any]) -> Decimal | None:
    net_amount = _decimal(activity.get("net_amount"))
    if net_amount is not None and net_amount != ZERO:
        return abs(net_amount)
    quantity = _decimal(activity.get("qty"))
    price = _decimal(activity.get("price"))
    if quantity is not None and price is not None:
        return abs(quantity * price)
    return None


def _external_cash_flow(
    activity: Mapping[str, Any],
) -> tuple[Decimal | None, str | None]:
    activity_type = str(activity.get("activity_type", "")).upper()
    description = str(activity.get("description", "")).lower()
    amount = _activity_amount(activity)
    if activity_type in CASH_DEPOSIT_TYPES:
        return amount, None if amount is not None else "missing_amount"
    if activity_type in CASH_WITHDRAWAL_TYPES:
        return (-amount if amount is not None else None), (
            None if amount is not None else "missing_amount"
        )
    if activity_type not in TRANSFER_TYPES:
        return None, None
    if "deposit" in description or "incoming" in description:
        return amount, None if amount is not None else "missing_amount"
    if "withdraw" in description or "outgoing" in description:
        return (-amount if amount is not None else None), (
            None if amount is not None else "missing_amount"
        )
    net_amount = _decimal(activity.get("net_amount"))
    if net_amount is not None and net_amount != ZERO:
        return net_amount, None
    return None, "ambiguous_transfer_direction"


def _portfolio_points(history: object) -> list[tuple[date, Decimal]]:
    timestamps = _field(history, "timestamp", [])
    equities = _field(history, "equity", [])
    if not isinstance(timestamps, list) or not isinstance(equities, list):
        return []
    by_date: dict[date, Decimal] = {}
    for raw_timestamp, raw_equity in zip(timestamps, equities):
        point_date = _history_date(raw_timestamp)
        equity = _decimal(raw_equity)
        if point_date is not None and equity is not None and equity >= ZERO:
            by_date[point_date] = equity
    return sorted(by_date.items())


def _account_activities(
    clients: AlpacaClients,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    page_token: str | None = None
    for _ in range(100):
        params: dict[str, Any] = {
            "after": start.isoformat(),
            "until": (end + timedelta(days=1)).isoformat(),
            "direction": "asc",
            "page_size": 100,
        }
        if page_token:
            params["page_token"] = page_token
        page = call_api(
            "get_account_activities_for_performance",
            clients.trading.get,
            "/account/activities",
            params,
        )
        if not isinstance(page, list):
            break
        normalized = [
            dict(item) for item in page if isinstance(item, Mapping)
        ]
        result.extend(normalized)
        if len(normalized) < 100:
            break
        next_token = str(normalized[-1].get("id", "")).strip()
        if not next_token or next_token == page_token:
            break
        page_token = next_token
    return result


def build_performance_summary(
    *,
    clients: AlpacaClients,
    run_date: str,
    current_equity: object,
) -> dict[str, Any]:
    """Fetch read-only facts and calculate linked daily returns."""

    end_date = date.fromisoformat(run_date)
    equity_now = _decimal(current_equity)
    history = call_api(
        "get_portfolio_history_for_performance",
        clients.trading.get_portfolio_history,
        GetPortfolioHistoryRequest(
            period="all",
            timeframe="1D",
            cashflow_types="ALL",
        ),
    )
    points = _portfolio_points(history)
    if equity_now is not None and equity_now >= ZERO:
        by_date = dict(points)
        by_date[end_date] = equity_now
        points = sorted(by_date.items())
    if not points:
        raise ValueError("portfolio history has no usable equity points")

    activities = _account_activities(
        clients,
        start=points[0][0],
        end=end_date,
    )
    flows_by_date: dict[date, Decimal] = defaultdict(lambda: ZERO)
    flow_types_by_date: dict[date, set[str]] = defaultdict(set)
    flow_timing_by_date: dict[date, str] = {}
    warnings: list[dict[str, Any]] = []
    recognized_count = 0
    for activity in activities:
        activity_type = str(activity.get("activity_type", "")).upper()
        amount, issue = _external_cash_flow(activity)
        if issue is not None:
            warnings.append(
                {
                    "code": "EXTERNAL_FLOW_ACTIVITY_PARTIAL",
                    "activity_type": activity_type or "unknown",
                    "reason": issue,
                }
            )
        if amount is None:
            continue
        occurred = _activity_datetime(activity)
        if occurred is None:
            warnings.append(
                {
                    "code": "EXTERNAL_FLOW_DATE_MISSING",
                    "activity_type": activity_type or "unknown",
                }
            )
            continue
        activity_date = occurred.date()
        if activity_date < points[0][0] or activity_date > end_date:
            continue
        flows_by_date[activity_date] += amount
        flow_types_by_date[activity_date].add(activity_type or "unknown")
        recognized_count += 1
        if occurred.time() > time(9, 30):
            flow_timing_by_date[activity_date] = "intraday_approximated_at_start"
            warnings.append(
                {
                    "code": "INTRADAY_FLOW_TIMING_APPROXIMATED",
                    "date": activity_date.isoformat(),
                    "activity_type": activity_type or "unknown",
                }
            )
        else:
            flow_timing_by_date.setdefault(
                activity_date, "before_or_at_market_open"
            )

    cumulative_factor = ONE
    previous_equity: Decimal | None = None
    daily_points: list[dict[str, Any]] = []
    for point_date, equity in points:
        external_flow = flows_by_date.get(point_date, ZERO)
        daily_return: Decimal | None = None
        if previous_equity is not None or external_flow > ZERO:
            denominator = (
                previous_equity + external_flow
                if previous_equity is not None
                else external_flow
            )
            if denominator > ZERO:
                daily_return = equity / denominator - ONE
                cumulative_factor *= ONE + daily_return
            elif equity != ZERO:
                warnings.append(
                    {
                        "code": "NON_POSITIVE_TWR_DENOMINATOR",
                        "date": point_date.isoformat(),
                    }
                )
        daily_points.append(
            {
                "date": point_date.isoformat(),
                "equity": _decimal_text(equity),
                "external_cash_flow": _decimal_text(external_flow),
                "external_cash_flow_types": sorted(
                    flow_types_by_date.get(point_date, set())
                ),
                "flow_timing": flow_timing_by_date.get(
                    point_date,
                    "none" if external_flow == ZERO else "unknown",
                ),
                "daily_twr": _decimal_text(daily_return),
                "cumulative_twr": _decimal_text(cumulative_factor - ONE),
            }
        )
        previous_equity = equity

    net_contributions = sum(flows_by_date.values(), ZERO)
    current_value = points[-1][1]
    net_profit = current_value - net_contributions
    status = "partial" if warnings else "complete"
    latest = daily_points[-1]
    return {
        "schema_version": "1.0",
        "status": status,
        "as_of_date": end_date.isoformat(),
        "generated_at": utc_now_iso(),
        "method": "linked_daily_returns_adjusted_for_external_cash_flows",
        "cash_flow_timing_assumption": "start_of_day_for_each_recognized_external_flow",
        "current_equity": _decimal_text(current_value),
        "net_contributions_total": _decimal_text(net_contributions),
        "net_profit_after_contributions": _decimal_text(net_profit),
        "daily_twr": latest["daily_twr"],
        "cumulative_twr": latest["cumulative_twr"],
        "history_start": points[0][0].isoformat(),
        "history_end": points[-1][0].isoformat(),
        "recognized_external_flow_count": recognized_count,
        "daily_points": daily_points,
        "warnings": warnings,
        "errors": [],
    }


def safe_performance_summary(
    *,
    clients: AlpacaClients,
    run_date: str,
    current_equity: object,
) -> dict[str, Any]:
    """Return an explicit unavailable result instead of blocking trading."""

    try:
        return build_performance_summary(
            clients=clients,
            run_date=run_date,
            current_equity=current_equity,
        )
    except Exception as error:
        return {
            "schema_version": "1.0",
            "status": "unavailable",
            "as_of_date": run_date,
            "generated_at": utc_now_iso(),
            "method": "linked_daily_returns_adjusted_for_external_cash_flows",
            "cash_flow_timing_assumption": "unavailable",
            "current_equity": (
                _decimal_text(_decimal(current_equity))
            ),
            "net_contributions_total": None,
            "net_profit_after_contributions": None,
            "daily_twr": None,
            "cumulative_twr": None,
            "history_start": None,
            "history_end": None,
            "recognized_external_flow_count": 0,
            "daily_points": [],
            "warnings": [],
            "errors": [
                {
                    "code": "PERFORMANCE_DATA_UNAVAILABLE",
                    "exception_type": error.__class__.__name__,
                }
            ],
        }
