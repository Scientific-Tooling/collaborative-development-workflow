from __future__ import annotations

import signal
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def fail_if_call_blocks(seconds: float = 5.0) -> Iterator[None]:
    """Fail a short in-process test call instead of letting it block forever."""
    required = ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
    if not all(hasattr(signal, name) for name in required):
        raise unittest.SkipTest("real-time signal timers are not available")

    def fail_on_alarm(_signal_number: int, _frame: object) -> None:
        raise AssertionError(
            f"in-process test call did not finish within {seconds:g} seconds"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    started = time.monotonic()
    try:
        signal.signal(signal.SIGALRM, fail_on_alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        delay, interval = previous_timer
        if delay:
            delay = max(delay - (time.monotonic() - started), 1e-6)
        signal.setitimer(signal.ITIMER_REAL, delay, interval)
