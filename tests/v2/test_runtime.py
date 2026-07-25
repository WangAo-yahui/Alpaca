from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from v2.runtime import (
    atomic_write_json,
    build_cycle_paths,
    build_daily_paths,
    create_cycle_paths,
    list_cycle_ids,
    load_json_object,
    normalize_cycle_id,
    normalize_run_date,
)


class RuntimeTests(unittest.TestCase):
    def test_normalize_new_york_date(self) -> None:
        value = datetime(
            2026,
            7,
            24,
            2,
            tzinfo=ZoneInfo("UTC"),
        )
        self.assertEqual(
            normalize_run_date(value),
            "2026-07-23",
        )
        with self.assertRaises(ValueError):
            normalize_run_date("2026-02-30")

    def test_cycle_id_validation(self) -> None:
        self.assertEqual(
            normalize_cycle_id("20260723T120502"),
            "20260723T120502",
        )
        with self.assertRaises(ValueError):
            normalize_cycle_id("20260723T250000")

    def test_canonical_paths_include_validation_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            daily = build_daily_paths(
                "2026-07-23",
                project_root=root,
            )
            cycle = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T120502",
                project_root=root,
            )

            self.assertEqual(
                daily.coarse_validation.name,
                "validation.json",
            )
            self.assertEqual(
                cycle.portfolio_validation.name,
                "validation.json",
            )
            self.assertEqual(
                cycle.execution_validation.name,
                "validation.json",
            )
            self.assertEqual(
                cycle.reconciliation.name,
                "reconciliation.json",
            )

    def test_create_cycle_is_unique_with_same_clock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(
                2026,
                7,
                23,
                12,
                0,
                0,
                tzinfo=ZoneInfo("America/New_York"),
            )
            first = create_cycle_paths(
                "2026-07-23",
                project_root=root,
                now=now,
            )
            second = create_cycle_paths(
                "2026-07-23",
                project_root=root,
                now=now,
            )

            self.assertEqual(
                first.cycle_id,
                "20260723T120000",
            )
            self.assertEqual(
                second.cycle_id,
                "20260723T120001",
            )
            self.assertEqual(
                list_cycle_ids(
                    "2026-07-23",
                    project_root=root,
                ),
                [first.cycle_id, second.cycle_id],
            )

    def test_atomic_json_replaces_complete_document(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(
                path,
                {"version": 2, "ok": True},
            )

            self.assertEqual(
                load_json_object(path),
                {"version": 2, "ok": True},
            )
            leftovers = list(
                path.parent.glob(".state.json.*.tmp")
            )
            self.assertEqual(leftovers, [])

    def test_load_json_requires_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(
                json.dumps([1, 2, 3]),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_json_object(path)


if __name__ == "__main__":
    unittest.main()
