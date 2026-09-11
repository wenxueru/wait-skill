#!/usr/bin/env python3
"""Wait for command-reported state and optionally wake a Codex thread once."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

EXIT_READY = 0
EXIT_TERMINAL = 2
EXIT_QUERY_FAILED = 3
EXIT_NOTIFY_FAILED = 70
EXIT_ALREADY_WATCHING = 75
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def extract_status(output: str, json_path: str | None = None) -> str:
    if json_path:
        value: object = json.loads(output)
        for key in json_path.split("."):
            if not key or not isinstance(value, dict) or key not in value:
                raise ValueError(f"JSON path not found: {json_path}")
            value = value[key]
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("status must be a scalar JSON value")
        status = str(value)
    else:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            raise ValueError("query produced no status")
        status = lines[-1]

    if len(status) > 128 or any(character in status for character in "\r\n{}[]"):
        raise ValueError("status must be a short, single scalar value")
    return status


def query_status(
    command: Sequence[str], query_timeout: float, json_path: str | None
) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=query_timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"query exited with status {result.returncode}")
    return extract_status(result.stdout, json_path)


def wait_for_status(
    query: Callable[[float | None], str],
    ready: set[str],
    terminal: set[str],
    *,
    interval: float,
    timeout: float | None,
    max_consecutive_failures: int,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], int]:
    started = now()
    deadline = started + timeout if timeout is not None else math.inf
    status: str | None = None
    failures = 0
    consecutive_failures = 0

    while True:
        remaining = None if timeout is None else deadline - now()
        if remaining is not None and remaining <= 0:
            event, code = "timeout", EXIT_TIMEOUT
            break
        try:
            candidate = query(remaining)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            if now() >= deadline:
                event, code = "timeout", EXIT_TIMEOUT
                break
            failures += 1
            consecutive_failures += 1
            if max_consecutive_failures and consecutive_failures >= max_consecutive_failures:
                event, code = "query_failed", EXIT_QUERY_FAILED
                break
        else:
            if now() >= deadline:
                event, code = "timeout", EXIT_TIMEOUT
                break
            status = candidate
            consecutive_failures = 0
            if status in ready:
                event, code = "ready", EXIT_READY
                break
            if status in terminal:
                event, code = "terminal", EXIT_TERMINAL
                break
        sleep(max(0.0, min(interval, deadline - now())))

    return {
        "event": event,
        "status": status,
        "query_failures": failures,
        "elapsed_seconds": round(now() - started, 3),
    }, code


def notify_thread(
    thread: str,
    remote: str,
    label: str,
    result: dict[str, object],
    template: str,
) -> None:
    fields = dict(result)
    fields["label"] = label
    message = template.format(**fields)
    subprocess.run(
        [
            "codex",
            "queue",
            "--remote",
            remote,
            "--thread",
            thread,
            "--message",
            message,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_result(path: Path | None, result: dict[str, object]) -> None:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    print(payload, flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--label", required=True, help="Safe human-readable object label")
    result.add_argument("--ready", action="append", required=True, help="Exact ready status; repeatable")
    result.add_argument("--terminal", action="append", default=[], help="Exact terminal status; repeatable")
    result.add_argument("--json-path", help="Dot-separated path to a scalar status in JSON stdout")
    result.add_argument("--interval", type=positive_number, default=300.0)
    result.add_argument("--query-timeout", type=positive_number, default=30.0)
    result.add_argument("--timeout", type=positive_number, help="Overall seconds; omitted means unlimited")
    result.add_argument(
        "--max-consecutive-failures",
        type=nonnegative_int,
        default=12,
        help="Wake after this many query failures; 0 disables the limit",
    )
    result.add_argument("--thread", help="Existing Codex thread ID to wake")
    result.add_argument("--remote", default="unix://", help="Codex app-server endpoint")
    result.add_argument("--lock-file", type=Path, help="Reject another watcher holding this lock")
    result.add_argument("--log-file", type=Path, help="Write the final JSON result here")
    result.add_argument(
        "--message-template",
        default=(
            "[Passive wait] {label}: {event}; status={status}; "
            "query failures={query_failures}. Re-check external state before continuing; "
            "do not assume permission to retry or mutate it."
        ),
        help="Python format string using label/event/status/query_failures/elapsed_seconds",
    )
    result.add_argument("command", nargs=argparse.REMAINDER, help="Read-only query command after --")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser().error("a query command is required after --")

    lock = None
    if args.lock_file:
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock = args.lock_file.open("a", encoding="utf-8")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_result(
                args.log_file,
                {"label": args.label, "event": "already_watching", "status": None},
            )
            return EXIT_ALREADY_WATCHING

    try:
        result, code = wait_for_status(
            lambda remaining: query_status(
                command,
                min(args.query_timeout, remaining)
                if remaining is not None
                else args.query_timeout,
                args.json_path,
            ),
            set(args.ready),
            set(args.terminal),
            interval=args.interval,
            timeout=args.timeout,
            max_consecutive_failures=args.max_consecutive_failures,
        )
    except KeyboardInterrupt:
        result, code = {
            "event": "interrupted",
            "status": None,
            "query_failures": 0,
            "elapsed_seconds": 0.0,
        }, EXIT_INTERRUPTED

    result["label"] = args.label
    if args.thread and code != EXIT_INTERRUPTED:
        try:
            notify_thread(args.thread, args.remote, args.label, result, args.message_template)
        except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
            result["notification"] = "failed_or_unconfirmed; not retried"
            code = EXIT_NOTIFY_FAILED
        else:
            result["notification"] = "queued"
    write_result(args.log_file, result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
