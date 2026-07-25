from __future__ import annotations

import unittest

from v2.data.positions import (
    normalize_positions,
)
from tests.v2.fakes import fake_position


class PositionNormalizationTests(unittest.TestCase):
    def test_empty_positions(self) -> None:
        self.assertEqual(
            normalize_positions([]),
            [],
        )

    def test_positions_are_sorted_and_numeric(
        self,
    ) -> None:
        result = normalize_positions(
            [
                fake_position("TSLA"),
                fake_position("MU"),
            ]
        )
        self.assertEqual(
            [item["symbol"] for item in result],
            ["MU", "TSLA"],
        )
        self.assertEqual(
            result[0]["quantity"],
            10.0,
        )
        self.assertEqual(
            result[0]["available_quantity"],
            8.0,
        )


if __name__ == "__main__":
    unittest.main()
