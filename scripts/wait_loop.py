#!/usr/bin/env python3
"""Persist a bounded timer loop driven by wait watcher events."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from collections import UserDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

CLIENTS = {"claude", "codewiz", "codex", "copilot", "cursor"}
DEFAULT_DURATION = 86400.0
DEFAULT_STATE_ROOT = Path("/tmp/.wait-loop")
STATUSES = {"active", "completed", "cancelled"}
PHASES = {"running", "waiting"}


class LoopError(ValueError):
    """Raised when a loop operation is invalid."""


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def default_state_path(start: Path | None = None) -> Path:
    project = (start or Path.cwd()).resolve()
    for candidate in (project, *project.parents):
        if (candidate / ".git").exists():
            project = candidate
            break
    project_name = (re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-.") or "project")[:48]
    project_hash = hashlib.sha256(os.fspath(project).encode()).hexdigest()[:12]
    return DEFAULT_STATE_ROOT / f"{project_name}-{project_hash}" / f"loop-{uuid.uuid4().hex[:8]}.json"


def validate(state: object) -> dict[str, object]:
    if not isinstance(state, dict):
        raise LoopError("state must be a JSON object")
    required = {
        "task",
        "client",
        "session",
        "interval_seconds",
        "deadline_at",
        "max_iterations",
        "status",
        "phase",
        "iteration",
        "next_run_at",
        "watch_id",
        "last_event_id",
        "runs",
        "events",
    }
    missing = required - state.keys()
    if missing:
        raise LoopError(f"state has missing fields: {sorted(missing)}")
    if not isinstance(state["task"], str) or not state["task"].strip():
        raise LoopError("task must be a non-empty string")
    if state["client"] not in CLIENTS:
        raise LoopError("invalid client")
    if not isinstance(state["session"], str) or not state["session"].strip():
        raise LoopError("session must be a non-empty string")
    if state["status"] not in STATUSES:
        raise LoopError("invalid status")
    if state["status"] == "active" and state["phase"] not in PHASES:
        raise LoopError("active loop must be running or waiting")
    if state["status"] != "active" and state["phase"] is not None:
        raise LoopError("terminal loop must not have a phase")
    for field in ("interval_seconds", "deadline_at"):
        value = state[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise LoopError(f"{field} must be a positive finite number")
    if not isinstance(state["iteration"], int) or state["iteration"] < 1:
        raise LoopError("iteration must be positive")
    limit = state["max_iterations"]
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise LoopError("max_iterations must be a positive integer or null")
    if not isinstance(state["runs"], list) or not isinstance(state["events"], list):
        raise LoopError("runs and events must be lists")
    waiting = state["status"] == "active" and state["phase"] == "waiting"
    if waiting:
        next_run = state["next_run_at"]
        if (
            not isinstance(state["watch_id"], str)
            or not state["watch_id"]
            or not isinstance(next_run, (int, float))
            or isinstance(next_run, bool)
            or not math.isfinite(next_run)
        ):
            raise LoopError("waiting loop must have a watch ID and next run time")
    elif state["watch_id"] is not None or state["next_run_at"] is not None:
        raise LoopError("only a waiting loop may have a watch ID or next run time")
    if state["last_event_id"] is not None and not isinstance(state["last_event_id"], str):
        raise LoopError("last_event_id must be a string or null")
    return state


class LoopState(UserDict[str, object]):
    """Loop state with its lifecycle transitions."""

    def __init__(self, state: object) -> None:
        super().__init__(validate(state))

    def append_event(self, operation: str, **details: object) -> None:
        events = self["events"]
        assert isinstance(events, list)
        events.append({"at": time.time(), "operation": operation, **details})

    def complete(self, summary: str) -> None:
        if self["status"] != "active" or self["phase"] != "running":
            raise LoopError("only a running iteration can complete")
        now = time.time()
        iteration = self["iteration"]
        runs = self["runs"]
        assert isinstance(runs, list)
        runs.append({"iteration": iteration, "summary": summary, "completed_at": now})
        limit = self["max_iterations"]
        if now >= self["deadline_at"] or isinstance(limit, int) and iteration >= limit:
            self.update(status="completed", phase=None, next_run_at=None, watch_id=None)
            self.append_event("finish", iteration=iteration)
            return
        watch_id = uuid.uuid4().hex
        self.update(
            phase="waiting",
            next_run_at=min(now + self["interval_seconds"], self["deadline_at"]),
            watch_id=watch_id,
        )
        self.append_event("schedule", iteration=iteration, watch_id=watch_id)

    def due(self, watch_id: str) -> str:
        if self["status"] == "completed":
            return "Completed"
        if self["status"] == "cancelled":
            return "Cancelled"
        if self["phase"] != "waiting" or self["watch_id"] != watch_id:
            return "Stale"
        now = time.time()
        if now >= self["deadline_at"]:
            return "Expired"
        return "Ready" if now >= self["next_run_at"] else "Waiting"

    def begin(self, event_id: str) -> bool:
        if self["last_event_id"] == event_id:
            return True
        self._require_active_wait(event_id)
        if time.time() >= self["deadline_at"]:
            self._expire(event_id)
        else:
            self["iteration"] = int(self["iteration"]) + 1
            self.update(phase="running", next_run_at=None, watch_id=None, last_event_id=event_id)
            self.append_event("begin", iteration=self["iteration"], event_id=event_id)
        return False

    def expire(self, event_id: str) -> bool:
        if self["last_event_id"] == event_id:
            return True
        self._require_active_wait(event_id)
        if time.time() < self["deadline_at"]:
            raise LoopError("loop duration has not expired")
        self._expire(event_id)
        return False

    def cancel(self) -> None:
        if self["status"] == "completed":
            raise LoopError("completed loop cannot be cancelled")
        self.update(status="cancelled", phase=None, next_run_at=None, watch_id=None)
        self.append_event("cancel")

    def _require_active_wait(self, event_id: str) -> None:
        if self["status"] != "active" or self["phase"] != "waiting":
            raise LoopError("loop is not waiting")
        if self["watch_id"] != event_id:
            raise LoopError("event ID does not match the active wait")

    def _expire(self, event_id: str) -> None:
        self.update(
            status="completed",
            phase=None,
            next_run_at=None,
            watch_id=None,
            last_event_id=event_id,
        )
        self.append_event("expire", event_id=event_id)


class LoopStore:
    """Serialize loop state changes under an advisory lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.realpath(os.path.abspath(os.path.expanduser(path))))

    def load(self) -> LoopState:
        try:
            return LoopState(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise LoopError(f"state file not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise LoopError(f"invalid state JSON: {exc}") from exc

    def write(self, state: LoopState) -> None:
        validate(state.data)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state.data, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)

    def create(self, state: LoopState) -> None:
        with self.locked():
            if self.path.exists():
                raise LoopError(f"state file already exists: {self.path}")
            self.write(state)

    @contextmanager
    def edit(self) -> Iterator[LoopState]:
        with self.locked():
            state = self.load()
            yield state  # noqa: RUF075 - failed edits must not be committed
            self.write(state)

    @contextmanager
    def locked(self) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def print_json(value: object) -> None:
    if isinstance(value, LoopState):
        value = value.data
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def command_init(args: argparse.Namespace) -> None:
    now = time.time()
    state = LoopState({
        "task": args.task,
        "client": args.client,
        "session": args.session,
        "interval_seconds": args.interval,
        "deadline_at": now + args.duration,
        "max_iterations": args.max_iterations,
        "status": "active",
        "phase": "running",
        "iteration": 1,
        "next_run_at": None,
        "watch_id": None,
        "last_event_id": None,
        "runs": [],
        "events": [{"at": now, "operation": "init"}],
    })
    store = LoopStore(args.state or default_state_path())
    store.create(state)
    print_json({"state_file": os.fspath(store.path), **state})


def command_complete(args: argparse.Namespace) -> None:
    store = LoopStore(args.state)
    with store.edit() as state:
        state.complete(args.summary)
    print_json(state)


def command_due(args: argparse.Namespace) -> None:
    state = LoopStore(args.state).load()
    print(state.due(args.watch_id))


def command_begin(args: argparse.Namespace) -> None:
    store = LoopStore(args.state)
    with store.edit() as state:
        if state.begin(args.event_id):
            print_json({"duplicate": True, "iteration": state["iteration"]})
            return
    print_json(state)


def command_expire(args: argparse.Namespace) -> None:
    store = LoopStore(args.state)
    with store.edit() as state:
        if state.expire(args.event_id):
            print_json({"duplicate": True, "status": state["status"]})
            return
    print_json(state)


def command_cancel(args: argparse.Namespace) -> None:
    store = LoopStore(args.state)
    with store.edit() as state:
        state.cancel()
    print_json(state)


def command_show(args: argparse.Namespace) -> None:
    print_json(LoopStore(args.state).load())


def add_state(command: argparse.ArgumentParser, *, required: bool = True) -> None:
    command.add_argument(
        "--state",
        type=Path,
        required=required,
        help="Loop JSON path; init defaults to a per-project path under /tmp/.wait-loop",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    add_state(init, required=False)
    init.add_argument("--task", required=True)
    init.add_argument("--interval", type=positive_number, required=True)
    init.add_argument("--duration", type=positive_number, default=DEFAULT_DURATION)
    init.add_argument("--max-iterations", type=positive_int)
    init.add_argument("--client", choices=sorted(CLIENTS), default="codex")
    init.add_argument("--session", "--thread", dest="session", required=True)
    init.set_defaults(handler=command_init)

    complete = commands.add_parser("complete")
    add_state(complete)
    complete.add_argument("--summary", required=True)
    complete.set_defaults(handler=command_complete)

    due = commands.add_parser("due")
    add_state(due)
    due.add_argument("--watch-id", required=True)
    due.set_defaults(handler=command_due)

    begin = commands.add_parser("begin")
    add_state(begin)
    begin.add_argument("--event-id", required=True)
    begin.set_defaults(handler=command_begin)

    expire = commands.add_parser("expire")
    add_state(expire)
    expire.add_argument("--event-id", required=True)
    expire.set_defaults(handler=command_expire)

    cancel = commands.add_parser("cancel")
    add_state(cancel)
    cancel.set_defaults(handler=command_cancel)

    show = commands.add_parser("show")
    add_state(show)
    show.set_defaults(handler=command_show)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except LoopError as exc:
        print_json({"error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
