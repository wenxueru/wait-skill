#!/usr/bin/env python3
"""Run one local service for wait watchers plus goal and loop commands."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

import wait_for
import wait_loop
from state_runner import StateCommandRunner
from wait_protocol import (
    PROTOCOL_VERSION,
    REGISTRY_PATH,
    SERVICE_FINGERPRINT,
    SOCKET_PATH,
    STATE_COMMANDS,
)

MAX_FINISHED_WATCHES = 256
PATH_OPTIONS = {"--goal-state", "--loop-state", "--startup-file", "--log-file", "--lock-file"}


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    wait_for.atomic_write_text(path, payload)
    path.chmod(0o600)


def absolute_wait_argv(argv: list[str], cwd: Path) -> list[str]:
    """Resolve watcher coordination paths against the submitting process."""

    def resolve_binding(match: re.Match[str]) -> str:
        return match[1] + os.fspath(cwd / match[2])

    result: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            result.extend(argv[index:])
            break
        option, separator, value = item.partition("=")
        if option == "--message-template" and (separator or index + 1 < len(argv)):
            template = value if separator else argv[index + 1]

            template = re.sub(
                r"([$/](?:wait|wait-goal|wait-loop)\s+resume\s+|(?<![\w.-])watcher_log=)(.+?)(?=\s*;|$)",
                resolve_binding,
                template,
            )
            result.extend(("--message-template", template))
            if not separator:
                index += 1
        elif option in PATH_OPTIONS and separator:
            result.append(f"{option}={cwd / value}")
        elif item in PATH_OPTIONS and index + 1 < len(argv):
            result.extend((item, os.fspath(cwd / argv[index + 1])))
            index += 1
        else:
            result.append(item)
        index += 1
    return result


def string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return value


def working_directory(cwd: str) -> Path:
    path = Path(cwd)
    if not path.is_dir():
        raise ValueError(f"working directory does not exist: {cwd}")
    return path


def unlink_if_present(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def acquire_daemon_lock(socket_path: Path) -> TextIO:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket_path.parent.chmod(0o700)
    lock = socket_path.with_name("waitd.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unlink_if_present(socket_path)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("waitd is already running") from exc
    except OSError:
        lock.close()
        raise
    return lock


async def wait_for_cancel(cancel_event: asyncio.Event, timeout: float) -> bool:
    """Return whether cancellation arrives before the timeout."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout)
    except asyncio.TimeoutError:
        return False
    return True


async def query_status(command: list[str], timeout: float, json_path: str | None, cwd: str) -> str:
    """Own the query process until output, timeout, or cancellation is resolved."""
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

    async def read() -> str:
        output = bytearray()
        assert process.stdout is not None
        while chunk := await process.stdout.read(8192):
            output.extend(chunk)
            if len(output) > wait_for.MAX_QUERY_OUTPUT_BYTES:
                raise ValueError("query output exceeds size limit")
        if await process.wait():
            raise RuntimeError("query command failed")
        return wait_for.extract_status(output.decode("utf-8"), json_path)

    try:
        return await asyncio.wait_for(read(), timeout)
    finally:
        # Descendants may outlive their parent, including after stdout closes.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


class WaitDaemon:
    """Persistent watcher scheduler and state-command gateway."""

    def __init__(self, registry_path: Path = REGISTRY_PATH) -> None:
        self.registry_path = registry_path
        self.loops: dict[str, str] = {}
        self.watchers: dict[str, dict[str, Any]] = self._load_registry()
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.state_commands = StateCommandRunner()
        self.shutdown_event = asyncio.Event()
        self.changed = asyncio.Event()

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        watchers = value.get("watchers") if isinstance(value, dict) else None
        if not isinstance(watchers, dict):
            raise ValueError(f"invalid waitd registry: {self.registry_path}")
        self.loops = value.get("loops", {})
        if not isinstance(self.loops, dict) or any(not isinstance(v, str) for v in self.loops.values()):
            raise ValueError("invalid loop registry")
        return watchers

    def save(self) -> None:
        # Keep consumed timer IDs while their loop still awaits the root's acknowledgement.
        protected = set()
        for filename in self.loops:
            try:
                protected.add(wait_loop.LoopStore(Path(filename)).load()["watch_id"])
            except (OSError, wait_loop.LoopError):
                continue
        finished = sorted(
            (
                watch_id
                for watch_id, record in self.watchers.items()
                if record.get("state") != "active" and watch_id not in protected
            ),
            key=lambda watch_id: float(self.watchers[watch_id].get("updated_at", 0)),
            reverse=True,
        )
        for watch_id in finished[MAX_FINISHED_WATCHES:]:
            del self.watchers[watch_id]
        atomic_write_json(self.registry_path, {"watchers": self.watchers, "loops": self.loops})

    def _update_record(self, record: dict[str, Any], **changes: object) -> None:
        record.update(changes, updated_at=time.time())
        self.save()
        self.changed.set()
        self.changed = asyncio.Event()

    def _persist_result(
        self,
        args: argparse.Namespace,
        record: dict[str, Any],
        result: dict[str, object],
        **changes: object,
    ) -> None:
        self._update_record(record, result=result, **changes)
        wait_for.persist_result(args.log_file, result)

    async def restore(self) -> None:
        for watch_id, record in self.watchers.items():
            if record.get("state") == "active":
                self._schedule(watch_id)
        await self.reconcile_loops()

    async def reconcile_loops(self) -> list[dict[str, object]]:
        """Repair the state-commit/timer-registration window from durable loop files."""
        monitors: list[dict[str, object]] = []
        for filename, cwd in list(self.loops.items()):
            store = wait_loop.LoopStore(Path(filename))
            if not store.path.exists():
                continue  # init may not have committed before the service stopped
            try:
                state = store.load()
            except (OSError, wait_loop.LoopError) as exc:
                monitors.append({"state_file": filename, "error": str(exc)})
                continue
            if state["status"] == "active" and not wait_loop.goal_is_open(state):
                outcome = await self.state_commands.run("loop", ["cancel", "--state", filename], Path(cwd))
                if outcome["code"]:
                    monitors.append({"state_file": filename, "error": outcome["error"] or outcome["output"]})
                    continue
                state = store.load()
            if state.get("goal"):
                monitors.append({"state_file": filename, "goal": state["goal"], "status": state["status"]})
            if state["status"] != "active" or state["phase"] != "waiting":
                continue
            watch_id = str(state["watch_id"])
            if watch_id in self.watchers:
                continue
            self._submit_loop_timer(state, store.path, filename, cwd)
        return monitors

    def _submit_loop_timer(self, state: wait_loop.LoopState, path: Path, filename: str, cwd: str) -> None:
        watch_id = str(state["watch_id"])
        log = path.with_name(f"{path.stem}-{watch_id}.watch.json")
        invocation = "$wait-loop" if state["client"] == "codex" else "/wait-loop"
        message = (
            f"{invocation} resume {filename}; watcher_log={log}; event_id={{event_id}}; event={{event}}; status={{status}}"
        )
        self.submit(
            [
                "--label",
                "loop timer",
                "--ready",
                "Ready",
                "--terminal",
                "Expired",
                "--interval",
                str(min(60.0, state["interval_seconds"])),
                "--timeout",
                str(max(1.0, state["deadline_at"] - time.time() + 60)),
                "--client",
                str(state["client"]),
                "--session",
                str(state["session"]),
                "--event-id",
                watch_id,
                "--loop-state",
                filename,
                "--log-file",
                str(log),
                "--lock-file",
                str(log.with_suffix(".lock")),
                "--message-template",
                message,
                "--",
                sys.executable,
                str(Path(wait_loop.__file__).resolve()),
                "due",
                "--state",
                filename,
                "--watch-id",
                watch_id,
            ],
            cwd,
        )

    def _schedule(self, watch_id: str) -> None:
        self.cancel_events[watch_id] = asyncio.Event()
        task = asyncio.create_task(self._run_watch(watch_id))
        self.tasks[watch_id] = task
        task.add_done_callback(lambda completed, current=watch_id: self._forget_task(current, completed))

    def _forget_task(self, watch_id: str, task: asyncio.Task[None]) -> None:
        if self.tasks.get(watch_id) is task:
            del self.tasks[watch_id]
            self.cancel_events.pop(watch_id, None)

    def submit(self, argv: list[str], cwd: str) -> dict[str, object]:
        directory = working_directory(cwd)
        resolved_argv = absolute_wait_argv(argv, directory)
        args, _ = wait_for.parse_wait_args(resolved_argv)
        watch_id = args.event_id or uuid.uuid4().hex
        if watch_id in self.watchers:
            raise ValueError(f"watch ID already exists: {watch_id}")
        if args.event_id is None:
            resolved_argv = ["--event-id", watch_id, *resolved_argv]
        now = time.time()
        deadline_at = now + args.timeout
        self.watchers[watch_id] = {
            "watch_id": watch_id,
            "argv": resolved_argv,
            "cwd": os.path.realpath(cwd),
            "state": "active",
            "phase": "activating" if args.goal_state else "querying",
            "submitted_at": now,
            "deadline_at": deadline_at,
            "updated_at": now,
        }
        if args.goal_state:
            self.watchers[watch_id]["activation_deadline_at"] = min(
                deadline_at,
                now + args.activation_timeout,
            )
        self.save()
        self._schedule(watch_id)
        return {"watch_id": watch_id, "state": "active"}

    def cancel(self, watch_id: str) -> dict[str, object]:
        record = self.watchers.get(watch_id)
        if record is None:
            raise ValueError(f"unknown watch: {watch_id}")
        if record.get("state") != "active":
            return {"watch_id": watch_id, "state": record.get("state"), "duplicate": True}
        self._update_record(record, state="cancelled")
        event = self.cancel_events.get(watch_id)
        if event is not None:
            event.set()
        return {"watch_id": watch_id, "state": "cancelled", "duplicate": False}

    async def dispatch(self, request: object) -> dict[str, object]:
        if not isinstance(request, dict):
            raise TypeError("request must be a JSON object")
        operation = request.get("operation")
        if operation == "ping":
            return {
                "status": "ok",
                "pid": os.getpid(),
                "protocol_version": PROTOCOL_VERSION,
                "service_fingerprint": SERVICE_FINGERPRINT,
            }
        if operation == "submit":
            return self.submit(
                string_list(request.get("argv"), "argv"),
                str(request.get("cwd", "")),
            )
        if operation == "list":
            return {"watchers": list(self.watchers.values())}
        if operation == "show":
            watch_id = str(request.get("watch_id", ""))
            if watch_id not in self.watchers:
                raise ValueError(f"unknown watch: {watch_id}")
            return self.watchers[watch_id]
        if operation == "cancel":
            return self.cancel(str(request.get("watch_id", "")))
        if operation == "follow":
            watch_id = str(request.get("watch_id", ""))
            timeout = float(request.get("timeout", 0))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("follow timeout must be positive and finite")
            try:
                return await asyncio.wait_for(self.follow(watch_id), timeout)
            except asyncio.TimeoutError as exc:
                raise ValueError("follow timed out; inspect the saved watch before resuming") from exc
        if isinstance(operation, str) and operation in STATE_COMMANDS:
            cwd = working_directory(str(request.get("cwd", "")))
            argv = string_list(request.get("argv"), "argv")
            if operation == "loop":
                parsed = wait_loop.parser().parse_args(argv)
                path = parsed.state or wait_loop.default_state_path(cwd)
                path = (cwd / path).resolve()
                if parsed.state is None:
                    argv = [*argv, "--state", str(path)]
                self.loops[str(path)] = str(cwd)
                self.save()  # record recovery intent before the state command commits
            response = await self.state_commands.run(operation, argv, cwd)
            monitors = await self.reconcile_loops()
            if monitors:
                response["loop_monitors"] = monitors
            return response
        if operation == "shutdown":
            self.shutdown_event.set()
            return {"status": "stopping"}
        raise ValueError(f"unknown operation: {operation}")

    async def follow(self, watch_id: str) -> dict[str, object]:
        """Block for a durable event, without waiting for a goal's wake acknowledgement."""
        while True:
            changed = self.changed
            record = self.watchers.get(watch_id)
            if record is None:
                raise ValueError(f"unknown watch: {watch_id}")
            result = record.get("result")
            if result is not None or record.get("state") != "active":
                break
            await changed.wait()

        args, _ = wait_for.parse_wait_args(record["argv"])
        resume_message = None
        if result and args.message_template:
            fields: dict[str, object] = dict.fromkeys(wait_for.MESSAGE_FIELDS)
            fields.update(result, label=args.label, event_id=watch_id)
            resume_message = args.message_template.format(**fields)
        return {
            "watch_id": watch_id,
            "state": record["state"],
            "result": result,
            "log_file": str(args.log_file) if args.log_file else None,
            "resume_message": resume_message,
        }

    async def _run_watch(self, watch_id: str) -> None:
        record = self.watchers[watch_id]
        try:
            args, command = wait_for.parse_wait_args(record["argv"])
            result, code = await self._watch(args, command, record, self.cancel_events[watch_id])
        except asyncio.CancelledError:
            return
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, SystemExit) as exc:
            result, code = {"event": "watcher_failed", "error": str(exc)}, 1
        if record.get("state") != "active":
            return
        self._update_record(
            record,
            state="completed" if code in {wait_for.EXIT_READY, wait_for.EXIT_TERMINAL} else "failed",
            code=code,
            result=result,
        )

    async def _watch(
        self,
        args: argparse.Namespace,
        command: list[str],
        record: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> tuple[dict[str, object], int]:
        lock = None
        if args.lock_file:
            args.lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock = args.lock_file.open("a", encoding="utf-8")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                return {"event": "already_watching", "status": None}, wait_for.EXIT_ALREADY_WATCHING
        try:
            phase = str(record.get("phase") or ("activating" if args.goal_state else "querying"))
            if phase == "activating":
                activation_deadline_at = float(
                    record.get(
                        "activation_deadline_at",
                        min(
                            float(record["deadline_at"]),
                            float(record["submitted_at"]) + args.activation_timeout,
                        ),
                    )
                )
                activation = await self._activate_goal_wait(
                    args,
                    cancel_event,
                    activation_deadline_at,
                )
                if activation != "active":
                    result = {
                        "event": f"activation_{activation}",
                        "status": None,
                        "label": args.label,
                        "event_id": args.event_id,
                    }
                    self._persist_result(args, record, result, phase="finalizing", code=wait_for.EXIT_ACTIVATION_CANCELLED)
                    return result, wait_for.EXIT_ACTIVATION_CANCELLED
                phase = "querying"
                self._update_record(record, phase=phase)

            if phase == "querying":
                result, code = await self._query_loop(
                    args,
                    command,
                    float(record["deadline_at"]),
                    float(record["submitted_at"]),
                    record["cwd"],
                    cancel_event,
                )
                if cancel_event.is_set() or not wait_for.watch_is_current(args):
                    result["event"] = "cancelled"
                    code = wait_for.EXIT_ACTIVATION_CANCELLED
                result.update(label=args.label, event_id=args.event_id)
                if args.thread and code != wait_for.EXIT_ACTIVATION_CANCELLED:
                    result.update(notification="pending", notification_attempts=0)
                    phase = "notifying"
                else:
                    phase = "finalizing"
                self._persist_result(args, record, result, phase=phase, code=code)
            else:
                saved_result = record.get("result")
                if not isinstance(saved_result, dict):
                    raise ValueError(f"watch {record['watch_id']} has no persisted result")
                result = saved_result
                code = int(record["code"])

            if phase == "notifying" and result.get("notification") == "attempting":
                result["notification"] = "unconfirmed"
                code = wait_for.EXIT_NOTIFY_FAILED
                phase = "finalizing"
                self._persist_result(args, record, result, phase=phase, code=code)

            if phase == "notifying":
                if result.get("notification") in {"queued", "native_pending", "completed"}:
                    delivery_code = None
                else:
                    delivery_code = await self._deliver(args, result, record, cancel_event)
                if delivery_code is not None:
                    code = delivery_code
                if delivery_code is None and args.goal_state:
                    phase = "awaiting_ack"
                    if result.get("notification") == "native_pending":
                        # Native task availability is not delivery confirmation.
                        _, timeout = wait_for.notification_limits(args)
                        deadline = float(result["available_at"]) + timeout
                    else:
                        deadline = float(result.get("delivered_at", time.time())) + args.wake_ack_timeout
                    record["wake_ack_deadline_at"] = deadline
                else:
                    phase = "finalizing"
                self._update_record(record, phase=phase, code=code)

            if phase == "awaiting_ack":
                await self._await_goal_wake(
                    args,
                    cancel_event,
                    float(record["wake_ack_deadline_at"]),
                )
                if result.get("notification") == "native_pending" and wait_for.goal_wait_is_current(args):
                    if cancel_event.is_set():
                        result["notification"], code = "cancelled", wait_for.EXIT_ACTIVATION_CANCELLED
                    else:
                        result["notification"], code = "unconfirmed", wait_for.EXIT_NOTIFY_FAILED
                self._persist_result(args, record, result, phase="finalizing", code=code)
            wait_for.persist_result(args.log_file, result)
            return result, code
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            result = {
                "event": "watcher_failed",
                "status": None,
                "label": args.label,
                "event_id": args.event_id,
                "error": str(exc),
            }
            # A write error must not hide the original failure in the registry.
            with suppress(OSError):
                wait_for.persist_result(args.log_file, result)
            return result, 1
        finally:
            if lock is not None:
                lock.close()

    async def _activate_goal_wait(
        self,
        args: argparse.Namespace,
        cancel_event: asyncio.Event,
        deadline_at: float,
    ) -> str:
        wait_for.persist_result(
            args.startup_file,
            {
                "label": args.label,
                "event_id": args.event_id,
                "event": "watcher_started",
                "goal_node": args.goal_node,
                "client": args.client,
                "thread": args.thread,
                "log_file": str(args.log_file),
                "lock_file": str(args.lock_file),
                "activation_deadline": deadline_at,
            },
        )
        while True:
            if cancel_event.is_set():
                return "cancelled"
            phase = wait_for.goal_wait_phase(args)
            if phase == "active":
                return "active"
            if phase == "invalid":
                return "cancelled"
            remaining = deadline_at - time.time()
            if remaining <= 0:
                return "timeout"
            if await wait_for_cancel(cancel_event, min(args.activation_interval, remaining)):
                return "cancelled"

    async def _query_loop(
        self,
        args: argparse.Namespace,
        command: list[str],
        deadline_at: float,
        submitted_at: float,
        cwd: str,
        cancel_event: asyncio.Event,
    ) -> tuple[dict[str, object], int]:
        status: str | None = None
        failures = consecutive_failures = 0
        error: str | None = None
        while True:
            if cancel_event.is_set() or not wait_for.watch_is_current(args):
                event, code = "cancelled", wait_for.EXIT_ACTIVATION_CANCELLED
                break
            remaining = deadline_at - time.time()
            if remaining <= 0:
                event, code = "timeout", wait_for.EXIT_TIMEOUT
                break
            try:
                candidate = await query_status(
                    command,
                    min(args.query_timeout, remaining),
                    args.json_path,
                    cwd,
                )
            except wait_for.QueryConfigurationError as exc:
                failures += 1
                error = str(exc)
                event, code = "query_failed", wait_for.EXIT_QUERY_FAILED
                break
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, asyncio.TimeoutError):
                failures += 1
                consecutive_failures += 1
                if consecutive_failures >= args.max_consecutive_failures:
                    event, code = "query_failed", wait_for.EXIT_QUERY_FAILED
                    break
            else:
                if time.time() >= deadline_at:
                    event, code = "timeout", wait_for.EXIT_TIMEOUT
                    break
                status = candidate
                consecutive_failures = 0
                if status in args.ready:
                    event, code = "ready", wait_for.EXIT_READY
                    break
                if status in args.terminal:
                    event, code = "terminal", wait_for.EXIT_TERMINAL
                    break
            delay = min(args.interval, max(0.0, deadline_at - time.time()))
            await wait_for_cancel(cancel_event, delay)
        result: dict[str, object] = {
            "event": event,
            "status": status,
            "query_failures": failures,
            "elapsed_seconds": round(time.time() - submitted_at, 3),
        }
        if error:
            result["error"] = error
        return result, code

    async def _deliver(
        self,
        args: argparse.Namespace,
        result: dict[str, object],
        record: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> int | None:
        if args.client == "claude":
            result.update(notification="native_pending", notification_attempts=0, available_at=time.time())
            self._persist_result(args, record, result)
            return None
        max_attempts, timeout = wait_for.notification_limits(args)
        previous_attempts = int(result.get("notification_attempts", 0))
        for attempt in range(previous_attempts + 1, max_attempts + 1):
            if cancel_event.is_set() or not wait_for.watch_is_current(args):
                result["notification"] = "cancelled"
                return wait_for.EXIT_ACTIVATION_CANCELLED
            result.update(notification="attempting", notification_attempts=attempt)
            self._persist_result(args, record, result)
            message = args.message_template.format(**result)
            command = wait_for.notification_command(
                args.client,
                args.thread,
                message,
                args.remote,
                args.resume_args,
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=record["cwd"],
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                result["notification"] = "failed"
                self._persist_result(args, record, result)
                return wait_for.EXIT_NOTIFY_FAILED
            try:
                returncode = await asyncio.wait_for(process.wait(), timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=0.2)
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                failed = "unconfirmed"
            else:
                if returncode == 0:
                    result.update(notification=wait_for.notification_success(args.client), delivered_at=time.time())
                    self._persist_result(args, record, result)
                    return None
                failed = "failed"
            exhausted = attempt == max_attempts
            result["notification"] = failed if exhausted else f"{failed}_retrying"
            self._persist_result(args, record, result)
            if exhausted:
                return wait_for.EXIT_NOTIFY_FAILED
            delay = min(args.notification_retry_interval * 2 ** min(attempt - 1, 10), 300.0)
            await wait_for_cancel(cancel_event, delay)
        return wait_for.EXIT_NOTIFY_FAILED

    async def _await_goal_wake(
        self,
        args: argparse.Namespace,
        cancel_event: asyncio.Event,
        deadline_at: float,
    ) -> None:
        while wait_for.goal_wait_is_current(args):
            if cancel_event.is_set():
                return
            remaining = deadline_at - time.time()
            if remaining <= 0:
                return
            if await wait_for_cancel(cancel_event, min(args.activation_interval, remaining)):
                return


async def handle_client(
    daemon: WaitDaemon,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        try:
            payload = await reader.readline()
            if len(payload) > 1024 * 1024:
                raise ValueError("request is too large")
            request = json.loads(payload)
            if isinstance(request, dict) and request.get("operation") == "follow":
                waiting = asyncio.create_task(daemon.dispatch(request))
                disconnected = asyncio.create_task(reader.read(1))
                try:
                    done, _ = await asyncio.wait({waiting, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                    if waiting not in done:
                        return
                    response = waiting.result()
                finally:
                    waiting.cancel()
                    disconnected.cancel()
                    await asyncio.gather(waiting, disconnected, return_exceptions=True)
            else:
                response = await daemon.dispatch(request)
            message = {"ok": True, **response}
        except (OSError, TypeError, ValueError, SystemExit) as exc:
            message = {"ok": False, "error": str(exc)}
        with suppress(ConnectionError):
            writer.write((json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n").encode())
            await writer.drain()
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


async def serve(socket_path: Path = SOCKET_PATH, registry_path: Path = REGISTRY_PATH) -> None:
    daemon_lock = acquire_daemon_lock(socket_path)
    try:
        daemon = WaitDaemon(registry_path)
        await daemon.restore()
        clients: set[asyncio.Task[None]] = set()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(handle_client(daemon, reader, writer))
            clients.add(task)
            task.add_done_callback(clients.discard)

        server = await asyncio.start_unix_server(
            accept,
            path=socket_path,
            limit=1024 * 1024 + 1,
        )
        await asyncio.to_thread(socket_path.chmod, 0o600)
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, daemon.shutdown_event.set)
        try:
            async with server:
                await daemon.shutdown_event.wait()
        finally:
            server.close()
            await server.wait_closed()
            tasks = list(daemon.tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if clients:
                _, pending = await asyncio.wait(clients, timeout=0.1)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await asyncio.to_thread(unlink_if_present, socket_path)
        daemon_lock.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("serve", nargs="?")
    result.add_argument("--socket", type=Path, default=SOCKET_PATH)
    result.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        asyncio.run(serve(args.socket, args.registry))
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
