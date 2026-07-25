from __future__ import annotations

import unittest

from v2.guidance import guidance_hash


class GuidanceSignatureTests(unittest.TestCase):
    def test_hash_is_stable_after_normalization(
        self,
    ) -> None:
        self.assertEqual(
            guidance_hash("  MU\r\n半导体  "),
            guidance_hash("MU\n半导体"),
        )
        self.assertNotEqual(
            guidance_hash("MU"),
            guidance_hash("TSLA"),
        )


if __name__ == "__main__":
    unittest.main()
