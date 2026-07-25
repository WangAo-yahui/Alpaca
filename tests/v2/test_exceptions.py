from __future__ import annotations

import unittest

from v2.exceptions import (
    ErrorCategory,
    LiveTradingRejected,
    TemporaryDataError,
    classify_exception,
)


class ExceptionTests(unittest.TestCase):
    def test_retryable_disposition(self) -> None:
        disposition = classify_exception(
            TemporaryDataError("quote unavailable")
        )
        self.assertEqual(
            disposition.category,
            ErrorCategory.RETRYABLE,
        )
        self.assertTrue(disposition.retryable)
        self.assertTrue(disposition.resume_allowed)

    def test_live_rejection_is_explicit(self) -> None:
        disposition = (
            LiveTradingRejected().disposition()
        )
        self.assertEqual(
            disposition.code,
            "LIVE_TRADING_REJECTED",
        )
        self.assertEqual(
            disposition.category,
            ErrorCategory.SAFETY_BLOCK,
        )

    def test_unknown_exception_fails_closed(
        self,
    ) -> None:
        disposition = classify_exception(
            RuntimeError("unexpected")
        )
        self.assertEqual(
            disposition.category,
            ErrorCategory.FATAL,
        )
        self.assertFalse(disposition.resume_allowed)


if __name__ == "__main__":
    unittest.main()
