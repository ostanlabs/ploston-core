"""Shared configuration types for AEL."""

from dataclasses import dataclass

from .enums import BackoffType


@dataclass
class RetryConfig:
    """Retry configuration for steps and tools."""

    max_attempts: int = 3
    backoff: BackoffType = BackoffType.FIXED
    delay_seconds: float = 1.0
