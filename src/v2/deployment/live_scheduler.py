"""Live 账户的纽约交易日动态调度与幂等槽位选择。

作用：把 Alpaca 交易日历转换为盘中小时槽位和收盘维护槽位，并在服务重启后
继续使用持久化认领记录避免重复执行。
重要性：调度层只能授予“何时可以尝试运行”，不能绕过主流程的交易门禁。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


NEW_YORK_TZ = ZoneInfo("America/New_York")
TERMINAL_SLOT_STATUSES = frozenset(
    {
        "completed",
        "failed_closed",
        "blocked_uncertain",
    }
)


@dataclass(frozen=True)
class FirstRunOffsetChange:
    """按交易日生效的首个盘中槽位偏移变更。"""

    effective_session_date: date
    minutes: int

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "FirstRunOffsetChange":
        change = cls(
            effective_session_date=_parse_date(
                payload["effective_session_date"]
            ),
            minutes=int(payload["minutes"]),
        )
        if change.minutes <= 0:
            raise ValueError(
                "Live首个运行偏移变更必须为正数"
            )
        return change


@dataclass(frozen=True)
class LiveScheduleSettings:
    timezone: str = "America/New_York"
    first_run_after_open_minutes: int = 15
    first_run_after_open_minutes_changes: (
        tuple[FirstRunOffsetChange, ...]
    ) = ()
    interval_minutes: int = 60
    last_run_before_close_minutes: int = 15
    close_check_after_minutes: int = 15
    grace_minutes: int = 20
    max_retriable_attempts: int = 2
    running_timeout_minutes: int = 45
    display_sleep_after_close: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
    ) -> "LiveScheduleSettings":
        source = payload or {}
        raw_changes = source.get(
            "first_run_after_open_minutes_changes",
            (),
        )
        if (
            not isinstance(raw_changes, Sequence)
            or isinstance(raw_changes, (str, bytes))
        ):
            raise ValueError(
                "Live首个运行偏移变更必须是数组"
            )
        changes = tuple(
            sorted(
                (
                    FirstRunOffsetChange.from_mapping(
                        item
                    )
                    for item in raw_changes
                    if isinstance(item, Mapping)
                ),
                key=lambda item: (
                    item.effective_session_date
                ),
            )
        )
        if len(changes) != len(raw_changes):
            raise ValueError(
                "Live首个运行偏移变更项必须是对象"
            )
        settings = cls(
            timezone=str(
                source.get("timezone", cls.timezone)
            ),
            first_run_after_open_minutes=int(
                source.get(
                    "first_run_after_open_minutes",
                    cls.first_run_after_open_minutes,
                )
            ),
            first_run_after_open_minutes_changes=changes,
            interval_minutes=int(
                source.get(
                    "interval_minutes",
                    cls.interval_minutes,
                )
            ),
            last_run_before_close_minutes=int(
                source.get(
                    "last_run_before_close_minutes",
                    cls.last_run_before_close_minutes,
                )
            ),
            close_check_after_minutes=int(
                source.get(
                    "close_check_after_minutes",
                    cls.close_check_after_minutes,
                )
            ),
            grace_minutes=int(
                source.get(
                    "grace_minutes",
                    cls.grace_minutes,
                )
            ),
            max_retriable_attempts=int(
                source.get(
                    "max_retriable_attempts",
                    cls.max_retriable_attempts,
                )
            ),
            running_timeout_minutes=int(
                source.get(
                    "running_timeout_minutes",
                    cls.running_timeout_minutes,
                )
            ),
            display_sleep_after_close=bool(
                source.get(
                    "display_sleep_after_close",
                    cls.display_sleep_after_close,
                )
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.timezone != "America/New_York":
            raise ValueError(
                "Live调度时区必须为America/New_York"
            )
        positive = (
            self.first_run_after_open_minutes,
            self.interval_minutes,
            self.last_run_before_close_minutes,
            self.close_check_after_minutes,
            self.grace_minutes,
            self.max_retriable_attempts,
            self.running_timeout_minutes,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Live调度参数必须为正数")
        if self.grace_minutes >= self.interval_minutes:
            raise ValueError(
                "Live调度宽限必须小于盘中运行间隔"
            )
        effective_dates = [
            change.effective_session_date
            for change in (
                self.first_run_after_open_minutes_changes
            )
        ]
        if len(effective_dates) != len(
            set(effective_dates)
        ):
            raise ValueError(
                "Live首个运行偏移变更生效日不得重复"
            )

    def first_run_minutes_for(
        self,
        session_date: date,
    ) -> int:
        minutes = self.first_run_after_open_minutes
        for change in (
            self.first_run_after_open_minutes_changes
        ):
            if (
                session_date
                < change.effective_session_date
            ):
                break
            minutes = change.minutes
        return minutes


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    open_at: datetime
    close_at: datetime

    def validate(self) -> None:
        if (
            self.open_at.tzinfo is None
            or self.close_at.tzinfo is None
            or self.close_at <= self.open_at
        ):
            raise ValueError("市场日历开收盘时间无效")
        if (
            self.open_at.astimezone(
                NEW_YORK_TZ
            ).date()
            != self.session_date
        ):
            raise ValueError("市场日历交易日与开盘日期不一致")

    def to_dict(self) -> dict[str, str]:
        return {
            "session_date": self.session_date.isoformat(),
            "open_at": self.open_at.astimezone(
                NEW_YORK_TZ
            ).isoformat(),
            "close_at": self.close_at.astimezone(
                NEW_YORK_TZ
            ).isoformat(),
        }

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "MarketSession":
        session = cls(
            session_date=date.fromisoformat(
                str(payload["session_date"])
            ),
            open_at=_parse_datetime(
                payload["open_at"]
            ),
            close_at=_parse_datetime(
                payload["close_at"]
            ),
        )
        session.validate()
        return session


@dataclass(frozen=True)
class ScheduleSlot:
    slot_id: str
    kind: str
    session_date: date
    scheduled_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "session_date": self.session_date.isoformat(),
            "scheduled_at": self.scheduled_at.astimezone(
                NEW_YORK_TZ
            ).isoformat(),
        }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace(
            "Z", "+00:00"
        )
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NEW_YORK_TZ)
    return parsed.astimezone(NEW_YORK_TZ)


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.astimezone(NEW_YORK_TZ).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _session_clock(
    value: Any,
    *,
    session_date: date,
) -> datetime:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=NEW_YORK_TZ
            )
        return parsed.astimezone(NEW_YORK_TZ)
    if isinstance(value, time):
        clock = value
    else:
        text = str(value).strip()
        try:
            return _parse_datetime(text)
        except ValueError:
            clock = time.fromisoformat(text)
    combined = datetime.combine(
        session_date,
        clock.replace(tzinfo=None),
        tzinfo=NEW_YORK_TZ,
    )
    return combined


def market_session_from_broker(
    payload: object,
) -> MarketSession:
    def field(name: str) -> Any:
        if isinstance(payload, Mapping):
            return payload.get(name)
        return getattr(payload, name, None)

    session_date = _parse_date(field("date"))
    session = MarketSession(
        session_date=session_date,
        open_at=_session_clock(
            field("open"),
            session_date=session_date,
        ),
        close_at=_session_clock(
            field("close"),
            session_date=session_date,
        ),
    )
    session.validate()
    return session


def build_session_slots(
    session: MarketSession,
    settings: LiveScheduleSettings,
) -> tuple[ScheduleSlot, ...]:
    session.validate()
    slots: list[ScheduleSlot] = []
    scheduled_at = session.open_at + timedelta(
        minutes=settings.first_run_minutes_for(
            session.session_date
        )
    )
    final_intraday = session.close_at - timedelta(
        minutes=settings.last_run_before_close_minutes
    )
    while scheduled_at <= final_intraday:
        slots.append(
            ScheduleSlot(
                slot_id=(
                    f"{session.session_date.isoformat()}"
                    f"_intraday_{scheduled_at:%H%M}"
                ),
                kind="intraday",
                session_date=session.session_date,
                scheduled_at=scheduled_at,
            )
        )
        scheduled_at += timedelta(
            minutes=settings.interval_minutes
        )
    close_at = session.close_at + timedelta(
        minutes=settings.close_check_after_minutes
    )
    slots.append(
        ScheduleSlot(
            slot_id=(
                f"{session.session_date.isoformat()}"
                f"_close_{close_at:%H%M}"
            ),
            kind="close",
            session_date=session.session_date,
            scheduled_at=close_at,
        )
    )
    return tuple(slots)


def all_slots(
    sessions: Sequence[MarketSession],
    settings: LiveScheduleSettings,
) -> tuple[ScheduleSlot, ...]:
    return tuple(
        slot
        for session in sorted(
            sessions,
            key=lambda item: item.open_at,
        )
        for slot in build_session_slots(
            session, settings
        )
    )


def _record_time(
    record: Mapping[str, Any],
    key: str,
) -> datetime | None:
    value = record.get(key)
    if not value:
        return None
    try:
        return _parse_datetime(value)
    except (TypeError, ValueError):
        return None


def slot_is_runnable(
    slot: ScheduleSlot,
    *,
    now: datetime,
    record: Mapping[str, Any] | None,
    settings: LiveScheduleSettings,
) -> bool:
    current = now.astimezone(NEW_YORK_TZ)
    if current < slot.scheduled_at:
        return False
    if current > slot.scheduled_at + timedelta(
        minutes=settings.grace_minutes
    ):
        return False
    existing = record or {}
    status = str(existing.get("status", ""))
    attempts = int(existing.get("attempts", 0) or 0)
    if status in TERMINAL_SLOT_STATUSES:
        return False
    if status == "running":
        claimed_at = _record_time(
            existing, "claimed_at"
        )
        if (
            claimed_at is not None
            and current
            < claimed_at
            + timedelta(
                minutes=(
                    settings.running_timeout_minutes
                )
            )
        ):
            return False
    if (
        status == "failed_retriable"
        and attempts
        >= settings.max_retriable_attempts
    ):
        return False
    return True


def select_due_slot(
    sessions: Sequence[MarketSession],
    *,
    now: datetime,
    slot_records: Mapping[str, Any],
    settings: LiveScheduleSettings,
) -> ScheduleSlot | None:
    due = [
        slot
        for slot in all_slots(sessions, settings)
        if slot_is_runnable(
            slot,
            now=now,
            record=(
                slot_records.get(slot.slot_id)
                if isinstance(
                    slot_records.get(slot.slot_id),
                    Mapping,
                )
                else None
            ),
            settings=settings,
        )
    ]
    return due[-1] if due else None


def next_schedule_slot(
    sessions: Sequence[MarketSession],
    *,
    now: datetime,
    slot_records: Mapping[str, Any],
    settings: LiveScheduleSettings,
) -> ScheduleSlot | None:
    current = now.astimezone(NEW_YORK_TZ)
    for slot in all_slots(sessions, settings):
        record = slot_records.get(slot.slot_id)
        status = (
            str(record.get("status", ""))
            if isinstance(record, Mapping)
            else ""
        )
        if (
            status not in TERMINAL_SLOT_STATUSES
            and slot.scheduled_at
            + timedelta(
                minutes=settings.grace_minutes
            )
            >= current
        ):
            return slot
    return None
