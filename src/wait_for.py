"""Reusable polling: the agent supplies query() and evaluate(data)."""

from __future__ import annotations

import math
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import TypeVar

Data = TypeVar("Data")
Result = TypeVar("Result")


class QueryFailed(RuntimeError):
    """The consecutive query failure limit was reached."""


class _DeadlineExpired(BaseException):
    """Escape a callback without being swallowed by except Exception."""


@contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    def expire(signum: int, frame: object) -> None:
        raise _DeadlineExpired

    previous = signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def poll(
    query: Callable[[], Data],
    evaluate: Callable[[Data], Result | None],
    *,
    interval: float = 300,
    timeout: float = 3600,
    query_timeout: float = 30,
    max_consecutive_failures: int = 12,
) -> Result:
    """Poll until evaluate returns anything other than None.

    query runs once per attempt. OSError, TimeoutError, and subprocess errors
    are retried up to the consecutive failure limit; other errors propagate.
    evaluate runs in the same process, so it can keep history between calls.
    Its exceptions propagate rather than being mistaken for query failures.

    Requires a Unix main thread with no existing ITIMER_REAL timer. Callbacks
    must not replace SIGALRM or manage ITIMER_REAL themselves. waitd provides
    the outer process timeout for code that cannot be interrupted by Python.
    """
    for name, value in (("interval", interval), ("timeout", timeout), ("query_timeout", query_timeout)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if isinstance(max_consecutive_failures, bool) or not isinstance(max_consecutive_failures, int):
        raise TypeError("max_consecutive_failures must be an integer")
    if max_consecutive_failures <= 0:
        raise ValueError("max_consecutive_failures must be positive")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("poll requires the main thread")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise RuntimeError("poll requires an unused ITIMER_REAL timer")

    deadline = time.monotonic() + timeout
    failures = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("poll timed out")
        try:
            with _time_limit(min(query_timeout, remaining)):
                data = query()
        except (_DeadlineExpired, OSError, subprocess.SubprocessError) as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError("poll timed out") from None
            failures += 1
            if failures >= max_consecutive_failures:
                raise QueryFailed(f"query failed {failures} consecutive times") from exc
        else:
            failures = 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("poll timed out")
            try:
                with _time_limit(remaining):
                    result = evaluate(data)
            except _DeadlineExpired:
                raise TimeoutError("poll timed out") from None
            if time.monotonic() >= deadline:
                raise TimeoutError("poll timed out")
            if result is not None:
                return result
        time.sleep(max(0, min(interval, deadline - time.monotonic())))


def run_command(command: Sequence[str], *, timeout: float = 30) -> str:
    """Run a read-only query without a shell; return stdout unchanged.

    Enforce a per-command timeout and reap the query process on every exit,
    including when poll interrupts the query. Stderr is inherited by the waiting
    program, so waitd can capture it. Keep query output bounded at its source.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return output.decode("utf-8")
    finally:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


if __name__ == "__main__":
    raise SystemExit("Import poll(query, evaluate) in your waiting script; wait_for.py is no longer a CLI.")
