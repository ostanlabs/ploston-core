"""#4 calculate_retry_delay: cap exponential backoff and keep FIXED unchanged.

Exponential backoff was unbounded (delay_seconds * 2**(attempt-1)); for large
attempt counts this could sleep for minutes. We clamp to a max and may add a
small jitter. The FIXED path must remain exact.
"""

from ploston_core.engine.types import MAX_RETRY_DELAY_SECONDS, calculate_retry_delay
from ploston_core.types import BackoffType, RetryConfig


def test_fixed_backoff_is_unchanged():
    cfg = RetryConfig(max_attempts=5, backoff=BackoffType.FIXED, delay_seconds=2.5)
    for attempt in range(1, 6):
        assert calculate_retry_delay(attempt, cfg) == 2.5


def test_exponential_backoff_clamped_for_large_attempts():
    cfg = RetryConfig(max_attempts=20, backoff=BackoffType.EXPONENTIAL, delay_seconds=1.0)
    # attempt 30 -> 2**29 seconds uncapped; must be clamped at the max.
    delay = calculate_retry_delay(30, cfg)
    # Allow a small jitter overshoot but never exceed max by more than ~10%.
    assert delay <= MAX_RETRY_DELAY_SECONDS * 1.1
    assert delay >= MAX_RETRY_DELAY_SECONDS * 0.5


def test_exponential_early_attempts_grow():
    cfg = RetryConfig(max_attempts=10, backoff=BackoffType.EXPONENTIAL, delay_seconds=1.0)
    # attempt 1 -> ~1s, attempt 2 -> ~2s (jitter-tolerant ordering check via base).
    d1 = calculate_retry_delay(1, cfg)
    d3 = calculate_retry_delay(3, cfg)
    assert d1 >= 1.0
    assert d3 >= 4.0
    assert d3 <= MAX_RETRY_DELAY_SECONDS * 1.1
