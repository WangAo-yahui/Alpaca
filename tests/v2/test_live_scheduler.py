"""验证 live1 纽约交易日动态调度、幂等认领和重试边界。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from v2.deployment.constants import ExitCode
from v2.deployment.live_scheduler import (
    LiveScheduleSettings,
    MarketSession,
    ScheduleSlot,
    build_session_slots,
    market_session_from_broker,
    next_schedule_slot,
    select_due_slot,
)
from v2.deployment.manager import DeploymentManager
from v2.deployment.release import ReleaseArtifact


NY = ZoneInfo("America/New_York")


def session(
    *,
    day: date = date(2026, 7, 27),
    close_hour: int = 16,
) -> MarketSession:
    return MarketSession(
        session_date=day,
        open_at=datetime(
            day.year,
            day.month,
            day.day,
            9,
            30,
            tzinfo=NY,
        ),
        close_at=datetime(
            day.year,
            day.month,
            day.day,
            close_hour,
            0,
            tzinfo=NY,
        ),
    )


class LiveSchedulerTests(unittest.TestCase):
    def test_regular_session_builds_expected_slots(
        self,
    ) -> None:
        slots = build_session_slots(
            session(), LiveScheduleSettings()
        )
        self.assertEqual(
            [
                slot.scheduled_at.strftime("%H:%M")
                for slot in slots
            ],
            [
                "09:45",
                "10:45",
                "11:45",
                "12:45",
                "13:45",
                "14:45",
                "15:45",
                "16:15",
            ],
        )
        self.assertEqual(slots[-1].kind, "close")

    def test_early_close_moves_close_check(
        self,
    ) -> None:
        slots = build_session_slots(
            session(close_hour=13),
            LiveScheduleSettings(),
        )
        self.assertEqual(
            [
                slot.scheduled_at.strftime("%H:%M")
                for slot in slots
            ],
            [
                "09:45",
                "10:45",
                "11:45",
                "12:45",
                "13:15",
            ],
        )
        self.assertEqual(slots[-1].kind, "close")

    def test_first_run_offset_change_starts_on_effective_session(
        self,
    ) -> None:
        settings = LiveScheduleSettings.from_mapping(
            {
                "first_run_after_open_minutes": 15,
                "first_run_after_open_minutes_changes": [
                    {
                        "effective_session_date": (
                            "2026-07-29"
                        ),
                        "minutes": 30,
                    }
                ],
            }
        )
        before = build_session_slots(
            session(day=date(2026, 7, 28)),
            settings,
        )
        effective = build_session_slots(
            session(day=date(2026, 7, 29)),
            settings,
        )
        self.assertEqual(
            [
                slot.scheduled_at.strftime("%H:%M")
                for slot in before
            ],
            [
                "09:45",
                "10:45",
                "11:45",
                "12:45",
                "13:45",
                "14:45",
                "15:45",
                "16:15",
            ],
        )
        self.assertEqual(
            [
                slot.scheduled_at.strftime("%H:%M")
                for slot in effective
            ],
            [
                "10:00",
                "11:00",
                "12:00",
                "13:00",
                "14:00",
                "15:00",
                "16:15",
            ],
        )

    def test_first_run_offset_change_rejects_duplicate_date(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "生效日不得重复",
        ):
            LiveScheduleSettings.from_mapping(
                {
                    "first_run_after_open_minutes_changes": [
                        {
                            "effective_session_date": (
                                "2026-07-29"
                            ),
                            "minutes": 30,
                        },
                        {
                            "effective_session_date": (
                                "2026-07-29"
                            ),
                            "minutes": 45,
                        },
                    ]
                }
            )

    def test_broker_calendar_normalizes_dst(
        self,
    ) -> None:
        summer = market_session_from_broker(
            SimpleNamespace(
                date=date(2026, 7, 27),
                open="09:30",
                close="16:00",
            )
        )
        winter = market_session_from_broker(
            SimpleNamespace(
                date=date(2026, 12, 1),
                open="09:30",
                close="16:00",
            )
        )
        self.assertEqual(
            summer.open_at.utcoffset().total_seconds(),
            -4 * 3600,
        )
        self.assertEqual(
            winter.open_at.utcoffset().total_seconds(),
            -5 * 3600,
        )

    def test_completed_slot_is_not_selected_again(
        self,
    ) -> None:
        settings = LiveScheduleSettings()
        now = datetime(
            2026, 7, 27, 9, 50, tzinfo=NY
        )
        due = select_due_slot(
            [session()],
            now=now,
            slot_records={},
            settings=settings,
        )
        assert due is not None
        self.assertEqual(due.slot_id, "2026-07-27_intraday_0945")
        self.assertIsNone(
            select_due_slot(
                [session()],
                now=now,
                slot_records={
                    due.slot_id: {
                        "status": "completed",
                        "attempts": 1,
                    }
                },
                settings=settings,
            )
        )

    def test_retriable_slot_has_bounded_attempts(
        self,
    ) -> None:
        settings = LiveScheduleSettings()
        now = datetime(
            2026, 7, 27, 9, 50, tzinfo=NY
        )
        slot_id = "2026-07-27_intraday_0945"
        self.assertIsNotNone(
            select_due_slot(
                [session()],
                now=now,
                slot_records={
                    slot_id: {
                        "status": "failed_retriable",
                        "attempts": 1,
                    }
                },
                settings=settings,
            )
        )
        self.assertIsNone(
            select_due_slot(
                [session()],
                now=now,
                slot_records={
                    slot_id: {
                        "status": "failed_retriable",
                        "attempts": 2,
                    }
                },
                settings=settings,
            )
        )

    def test_next_slot_skips_terminal_records(
        self,
    ) -> None:
        settings = LiveScheduleSettings()
        slots = build_session_slots(
            session(), settings
        )
        result = next_schedule_slot(
            [session()],
            now=datetime(
                2026, 7, 27, 9, 50, tzinfo=NY
            ),
            slot_records={
                slots[0].slot_id: {
                    "status": "completed"
                }
            },
            settings=settings,
        )
        assert result is not None
        self.assertEqual(
            result.scheduled_at.strftime("%H:%M"),
            "10:45",
        )

    def test_live_service_routes_intraday_and_close(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = (
                root
                / "config/v2/profiles/live1.json"
            )
            profile_path.parent.mkdir(
                parents=True
            )
            profile_path.write_text(
                """
{
  "profile_id": "live1",
  "environment": "live",
  "enabled": true,
  "credential_key_env": "LIVE_KEY",
  "credential_secret_env": "LIVE_SECRET",
  "strategy": {
    "strategy_id": "core_long",
    "strategy_version": "1.2.0"
  },
  "submission_policy": "alpaca_live@1.0.0"
}
""".strip(),
                encoding="utf-8",
            )
            manager = DeploymentManager(
                root,
                profile_id="live1",
                home=root / "home",
            )
            artifact = ReleaseArtifact(
                release_id="release",
                git_commit="a" * 40,
                root=root,
                manifest=root / "manifest.json",
                manifest_hash="b" * 64,
            )
            current = {"trading_enabled": True}
            intraday = ScheduleSlot(
                slot_id="intraday",
                kind="intraday",
                session_date=date.today(),
                scheduled_at=datetime.now(NY),
            )
            close = ScheduleSlot(
                slot_id="close",
                kind="close",
                session_date=date.today(),
                scheduled_at=datetime.now(NY),
            )
            for slot, allow_trade, maintenance in (
                (intraday, True, False),
                (close, False, True),
            ):
                with (
                    patch.object(
                        manager,
                        "_market_sessions",
                        return_value=[session()],
                    ),
                    patch(
                        "v2.deployment.manager.select_due_slot",
                        return_value=slot,
                    ),
                    patch.object(
                        manager,
                        "_run_application",
                        return_value=ExitCode.NO_ACTION,
                    ) as run,
                    patch.object(
                        manager,
                        "_latest_cycle_state",
                        return_value=None,
                    ),
                    patch.object(
                        manager,
                        "_close_check_can_sleep",
                        return_value=maintenance,
                    ),
                    patch.object(
                        manager,
                        "_screen_off",
                        return_value=True,
                    ) as screen_off,
                ):
                    result = (
                        manager._scheduled_live_service_run(
                            current=current,
                            artifact=artifact,
                        )
                    )
                self.assertEqual(
                    result, ExitCode.NO_ACTION
                )
                self.assertEqual(
                    run.call_args.kwargs["allow_trade"],
                    allow_trade,
                )
                self.assertEqual(
                    run.call_args.kwargs[
                        "maintenance_only"
                    ],
                    maintenance,
                )
                if maintenance:
                    screen_off.assert_called_once_with()
                else:
                    screen_off.assert_not_called()


if __name__ == "__main__":
    unittest.main()
