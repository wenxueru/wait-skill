#!/usr/bin/env python3
"""Control the local waitd watcher and state service."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from wait_protocol import (
    PROTOCOL_VERSION,
    RUNTIME_DIR,
    SERVICE_FINGERPRINT,
    SOCKET_PATH,
    STATE_COMMANDS,
)

DAEMON_SCRIPT = Path(__file__).with_name("waitd.py")
DAEMON_LOG = RUNTIME_DIR / "waitd.log"


def request(
    payload: dict[str, object],
    socket_path: Path = SOCKET_PATH,
    timeout: float = 5.0,
) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(os.fspath(socket_path))
        client.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
    value = json.loads(response)
    if not isinstance(value, dict):
        raise TypeError("waitd returned an invalid response")
    return value


def wait_for_daemon_stop(socket_path: Path = SOCKET_PATH, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    lock_path = socket_path.with_name("waitd.lock")
    while True:
        with lock_path.open("a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"waitd did not stop within {timeout:g} seconds")
        time.sleep(0.05)


def stop_daemon(socket_path: Path = SOCKET_PATH) -> dict[str, object]:
    response = request({"operation": "shutdown"}, socket_path)
    wait_for_daemon_stop(socket_path)
    return response


def service_matches(response: dict[str, object]) -> bool:
    return (
        response.get("protocol_version") == PROTOCOL_VERSION
        and response.get("service_fingerprint") == SERVICE_FINGERPRINT
    )


def start_daemon() -> dict[str, object]:
    try:
        response = request({"operation": "ping"})
    except OSError:
        pass
    else:
        if service_matches(response):
            return response
        stop_daemon()
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    RUNTIME_DIR.chmod(0o700)
    with DAEMON_LOG.open("ab") as log:
        subprocess.Popen(
            [sys.executable, os.fspath(DAEMON_SCRIPT), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            response = request({"operation": "ping"})
            if service_matches(response):
                return response
        except OSError:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"waitd did not start; inspect {DAEMON_LOG}")


def forwarded_args(values: list[str]) -> list[str]:
    return values[1:] if values[:1] == ["--"] else values


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    daemon = commands.add_parser("daemon")
    daemon.add_argument("action", choices=("start", "status", "stop"))
    start = commands.add_parser("start")
    start.add_argument("argv", nargs=argparse.REMAINDER)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("watch_id")
    follow = commands.add_parser("follow", help="Block for a durable event; use as a native background task")
    follow.add_argument("watch_id")
    follow.add_argument("--timeout", type=float, required=True)
    follow.add_argument("--socket", type=Path, default=SOCKET_PATH)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("watch_id")
    for name in STATE_COMMANDS:
        state = commands.add_parser(name, help=f"Run a {name} state command")
        state.add_argument("argv", nargs=argparse.REMAINDER)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "follow":
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ValueError("--timeout must be positive and finite")
            response = request(
                {"operation": "follow", "watch_id": args.watch_id, "timeout": args.timeout},
                args.socket, timeout=args.timeout + 5,
            )
        elif args.command == "daemon":
            if args.action == "start":
                response = start_daemon()
            elif args.action == "status":
                response = request({"operation": "ping"})
            else:
                response = stop_daemon()
        else:
            start_daemon()
            if args.command == "start":
                response = request(
                    {
                        "operation": "submit",
                        "argv": forwarded_args(args.argv),
                        "cwd": os.getcwd(),
                    },
                )
            elif args.command == "list":
                response = request({"operation": "list"})
            elif args.command == "show":
                response = request({"operation": "show", "watch_id": args.watch_id})
            elif args.command == "cancel":
                response = request({"operation": "cancel", "watch_id": args.watch_id})
            else:
                response = request(
                    {
                        "operation": args.command,
                        "argv": forwarded_args(args.argv),
                        "cwd": os.getcwd(),
                    },
                    timeout=35.0,
                )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0 if response.get("ok", False) and response.get("code", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
