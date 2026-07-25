from __future__ import annotations

import unittest
from pathlib import Path


class StageCSafetyTests(unittest.TestCase):
    def test_v2_has_no_order_submission_calls(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                root / "src" / "v2"
            ).rglob("*.py")
        )
        self.assertNotIn(
            "submit_order(",
            source,
        )
        self.assertNotIn(
            "place_order(",
            source,
        )
        self.assertNotIn(
            "from v1",
            source,
        )
        self.assertNotIn(
            "import v1",
            source,
        )


if __name__ == "__main__":
    unittest.main()
