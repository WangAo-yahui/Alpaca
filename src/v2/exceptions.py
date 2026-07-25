"""WA Trader v2 exception taxonomy.

The state machine uses these exception types to make failure handling
deterministic.  Callers should log ``code`` and the public message, not an
exception ``repr`` that could include a request payload or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    RETRYABLE = "retryable"
    SAFETY_BLOCK = "safety_block"
    FATAL = "fatal"


@dataclass(frozen=True)
class ErrorDisposition:
    category: ErrorCategory
    code: str
    message: str
    retryable: bool
    resume_allowed: bool
    normal_exit: bool
    details: dict[str, Any]


class V2Error(Exception):
    """Base class for errors safe to persist in v2 state files."""

    category = ErrorCategory.FATAL
    default_code = "V2_ERROR"
    retryable = False
    resume_allowed = False
    normal_exit = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        public_message = str(message).strip()
        if not public_message:
            raise ValueError("V2Error.message不能为空")

        super().__init__(public_message)
        self.public_message = public_message
        self.code = code or self.default_code
        self.details = dict(details or {})

    def disposition(self) -> ErrorDisposition:
        return ErrorDisposition(
            category=self.category,
            code=self.code,
            message=self.public_message,
            retryable=self.retryable,
            resume_allowed=self.resume_allowed,
            normal_exit=self.normal_exit,
            details=dict(self.details),
        )


class RetryableV2Error(V2Error):
    """Transient failure.  The current step may be retried on resume."""

    category = ErrorCategory.RETRYABLE
    default_code = "RETRYABLE_ERROR"
    retryable = True
    resume_allowed = True


class SafetyBlockedError(V2Error):
    """Expected risk/policy refusal; it is not an infrastructure crash."""

    category = ErrorCategory.SAFETY_BLOCK
    default_code = "SAFETY_BLOCKED"
    normal_exit = True


class FatalV2Error(V2Error):
    """Non-recoverable configuration, state, or identity failure."""

    category = ErrorCategory.FATAL
    default_code = "FATAL_ERROR"


class ConfigurationError(FatalV2Error):
    default_code = "CONFIGURATION_ERROR"


class StateValidationError(FatalV2Error):
    default_code = "STATE_VALIDATION_ERROR"


class LiveTradingRejected(SafetyBlockedError):
    default_code = "LIVE_TRADING_REJECTED"

    def __init__(self) -> None:
        super().__init__(
            "WA Trader v2初期只允许paper模式；--live已被拒绝"
        )


class TemporaryDataError(RetryableV2Error):
    default_code = "TEMPORARY_DATA_ERROR"


class CodexTimeoutError(RetryableV2Error):
    default_code = "CODEX_TIMEOUT"


class CodexOutputValidationError(RetryableV2Error):
    default_code = "CODEX_OUTPUT_INVALID"


class BrokerUnavailableError(RetryableV2Error):
    default_code = "BROKER_UNAVAILABLE"


def classify_exception(error: BaseException) -> ErrorDisposition:
    """Return the persisted failure policy for any exception.

    Unknown exceptions are deliberately fatal.  Treating an unknown coding
    error as retryable could otherwise produce an unbounded resume loop.
    """

    if isinstance(error, V2Error):
        return error.disposition()

    message = str(error).strip() or error.__class__.__name__
    return ErrorDisposition(
        category=ErrorCategory.FATAL,
        code="UNEXPECTED_ERROR",
        message=message,
        retryable=False,
        resume_allowed=False,
        normal_exit=False,
        details={"exception_type": error.__class__.__name__},
    )
