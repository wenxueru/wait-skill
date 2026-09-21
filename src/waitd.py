#!/usr/bin/env python3
"""Run one local service for wait watchers plus goal and loop commands."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import signal
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from collections.abc import Callable
from typing import Any, TextIO

import wait_loop
import wait_runtime
from wait_protocol import (
    PROTOCOL_VERSION,
    REGISTRY_PATH,
    SERVICE_FINGERPRINT,
    SOCKET_PATH,
    STATE_COMMANDS,
)

MAX_FINISHED_WATCHES = 256
MAX_PROGRAM_OUTPUT_BYTES = 64 * 1024
PATH_OPTIONS = {"--goal-state", "--loop-state", "--startup-file", "--log-file", "--lock-file"}
STATE_SCRIPTS = {name: Path(__file__).with_name(f"wait_{name}.py") for name in STATE_COMMANDS}
STATE_COMMAND_TIMEOUT = 30.0


class StateCommandRunner:
    """Run goal and loop state engines with independent serialization."""

    def __init__(self, socket_path: Path = SOCKET_PATH) -> None:
        # State commands reach back through this socket, e.g. to submit a watcher.
        self.socket_path = socket_path
        self.locks = {name: asyncio.Lock() for name in STATE_SCRIPTS}

    async def run(self, name: str, argv: list[str], cwd: Path) -> dict[str, object]:
        async def execute() -> tuple[bytes, bytes, int]:
            async with self.locks[name]:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    os.fspath(STATE_SCRIPTS[name]),
                    *argv,
                    cwd=cwd,
                    env={**os.environ, "WAITD_SOCKET": str(self.socket_path)},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                communication = asyncio.create_task(process.communicate())
                try:
                    stdout, stderr = await asyncio.shield(communication)
                except asyncio.CancelledError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await communication
                    raise
                assert process.returncode is not None
                return stdout, stderr, process.returncode

        try:
            stdout, stderr, returncode = await asyncio.wait_for(execute(), STATE_COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            return {
                "code": 124,
                "output": "",
                "error": f"{name} command exceeded {STATE_COMMAND_TIMEOUT:g} seconds",
            }
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()
        try:
            parsed: object = json.loads(output)
        except json.JSONDecodeError:
            parsed = output
        return {"code": returncode, "output": parsed, "error": error}


def absolute_wait_argv(argv: list[str], cwd: Path) -> list[str]:
    """Resolve watcher coordination paths against the submitting process."""

    result: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            result.extend(argv[index:])
            break
        option, separator, value = item.partition("=")
        if option in PATH_OPTIONS and separator:
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


async def run_program(
    command: list[str],
    timeout: float,
    cwd: str,
    on_started: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """Run once; preserve bounded output without interpreting it."""
    result: dict[str, object] = {"event": "exited", "exit_code": None}
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return {**result, "event": "start_failed", "error": str(exc), "stdout": "", "stderr": ""}
    if on_started:
        on_started(process.pid)

    async def drain(name: str, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(8192):
            remaining = MAX_PROGRAM_OUTPUT_BYTES - len(output[name])
            output[name].extend(chunk[:remaining])
            truncated[name] |= len(chunk) > remaining

    assert process.stdout is not None and process.stderr is not None
    readers = [
        asyncio.create_task(drain("stdout", process.stdout)),
        asyncio.create_task(drain("stderr", process.stderr)),
    ]
    try:
        await asyncio.wait_for(process.wait(), timeout)
        result["exit_code"] = process.returncode
    except asyncio.TimeoutError:
        result["event"] = "timeout"
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        await asyncio.gather(*readers)
    result.update({name: data.decode("utf-8", errors="replace") for name, data in output.items()})
    result["output_truncated"] = any(truncated.values())
    return result


class WaitDaemon:
    """Persistent watcher scheduler and state-command gateway."""

    def __init__(self, registry_path: Path = REGISTRY_PATH, socket_path: Path = SOCKET_PATH) -> None:
        self.registry_path = registry_path
        self.loops: dict[str, str] = {}
        self.watchers: dict[str, dict[str, Any]] = self._load_registry()
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.state_commands = StateCommandRunner(socket_path)
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
        wait_runtime.atomic_write_json(self.registry_path, {"watchers": self.watchers, "loops": self.loops})

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
        wait_runtime.persist_result(args.log_file, result)

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
            try:
                self.submit(wait_loop.timer_argv(state, store.path), cwd)
            except (OSError, ValueError, SystemExit) as exc:
                monitors.append({"state_file": filename, "error": str(exc)})
        return monitors

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
        wait_runtime.validate_job_argv(resolved_argv)
        options = wait_runtime.job_parser().parse_args(resolved_argv)
        if not options.goal_state and not options.loop_state:
            identity = json.dumps(
                [str(directory.resolve()), options.client, options.thread, options.remote, options.label, options.command],
                ensure_ascii=False,
            )
            lock_key = hashlib.sha256(identity.encode()).hexdigest()
            defaults = []
            if not options.lock_file:
                defaults += ["--lock-file", str(self.registry_path.parent / "watches" / f"{lock_key}.lock")]
            if not options.log_file:
                log_file = self.registry_path.parent / "watches" / f"{uuid.uuid4().hex}.json"
                defaults += ["--log-file", str(log_file)]
            resolved_argv = defaults + resolved_argv
        args, _ = wait_runtime.parse_job_args(resolved_argv)
        watch_id = args.event_id or uuid.uuid4().hex
        if watch_id in self.watchers:
            raise ValueError(f"watch ID already exists: {watch_id}")
        if args.event_id is None:
            resolved_argv = ["--event-id", watch_id, *resolved_argv]
        notification_channel: dict[str, object] = {"status": "disabled"}
        if args.client == "codex" and args.thread:
            try:
                remote = wait_runtime.preflight_codex_remote(args.remote)
            except wait_runtime.NotificationUnavailable as exc:
                wait_runtime.persist_result(
                    args.log_file,
                    {
                        "event": "notification_unavailable",
                        "query_status": "not_started",
                        "notification": "notification_unavailable",
                        "notification_stderr": str(exc),
                        "label": args.label,
                        "event_id": watch_id,
                        "log_file": str(args.log_file),
                    },
                )
                raise ValueError(str(exc)) from exc
            notification_channel = {
                "status": "ready" if remote.startswith("unix://") else "configured",
                "remote": remote,
            }
        elif args.client == "claude" and args.thread:
            notification_channel = {"status": "native_required"}
        now = time.time()
        deadline_at = now + args.timeout
        self.watchers[watch_id] = {
            "watch_id": watch_id,
            "argv": resolved_argv,
            "cwd": os.path.realpath(cwd),
            "state": "active",
            "phase": "activating" if args.goal_state else "queued",
            "submitted_at": now,
            "deadline_at": deadline_at,
            "updated_at": now,
            "query_status": "pending",
            "notification_channel": notification_channel,
            "session": args.thread,
        }
        if args.goal_state:
            self.watchers[watch_id]["activation_deadline_at"] = min(
                deadline_at,
                now + args.activation_timeout,
            )
        self.save()
        self._schedule(watch_id)
        return {
            "watch_id": watch_id,
            "state": "active",
            "log_file": str(args.log_file) if args.log_file else None,
            "lock_file": str(args.lock_file) if args.lock_file else None,
            "query_status": "pending",
            "notification_channel": notification_channel,
            "session": args.thread,
            "deadline": deadline_at,
        }

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

        args, _ = wait_runtime.parse_job_args(record["argv"])
        resume_message = wait_runtime.resume_instruction(args) if result and args.thread else None
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
            args, command = wait_runtime.parse_job_args(record["argv"])
            result, code = await self._watch(args, command, record, self.cancel_events[watch_id])
        except asyncio.CancelledError:
            return
        except (KeyError, OSError, RuntimeError, TypeError, ValueError, SystemExit) as exc:
            result, code = {"event": "watcher_failed", "error": str(exc)}, 1
        if record.get("state") != "active":
            return
        self._update_record(
            record,
            state="completed" if code == 0 else "failed",
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
                return {"event": "already_watching", "status": None}, wait_runtime.EXIT_ALREADY_WATCHING
            self._update_record(record, lock_status="held")
        try:
            phase = str(record.get("phase") or ("activating" if args.goal_state else "queued"))
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
                    self._persist_result(args, record, result, phase="finalizing", code=wait_runtime.EXIT_ACTIVATION_CANCELLED)
                    return result, wait_runtime.EXIT_ACTIVATION_CANCELLED
                phase = "queued"
                self._update_record(record, phase=phase)

            if phase in {"queued", "running", "querying"}:
                if phase == "queued":
                    self._update_record(record, phase="running")
                    result = await self._execute_program(args, command, record, cancel_event)
                else:
                    # An arbitrary program may already have acted before the service stopped.
                    result = {
                        "event": "interrupted",
                        "exit_code": None,
                        "stdout": "",
                        "stderr": "",
                        "error": "service restarted before completion was saved; not replayed",
                    }
                if result["event"] == "exited":
                    code = 0
                elif result["event"] == "timeout":
                    code = wait_runtime.EXIT_TIMEOUT
                else:
                    code = 1
                if cancel_event.is_set() or not wait_runtime.watch_is_current(args):
                    result["event"] = "cancelled"
                    code = wait_runtime.EXIT_ACTIVATION_CANCELLED
                result.update(label=args.label, event_id=args.event_id, log_file=str(args.log_file))
                if args.thread and code != wait_runtime.EXIT_ACTIVATION_CANCELLED:
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
                code = wait_runtime.EXIT_NOTIFY_FAILED
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
                        _, timeout = wait_runtime.notification_limits(args)
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
                if result.get("notification") == "native_pending" and wait_runtime.goal_wait_is_current(args):
                    if cancel_event.is_set():
                        result["notification"], code = "cancelled", wait_runtime.EXIT_ACTIVATION_CANCELLED
                    else:
                        result["notification"], code = "unconfirmed", wait_runtime.EXIT_NOTIFY_FAILED
                self._persist_result(args, record, result, phase="finalizing", code=code)
            wait_runtime.persist_result(args.log_file, result)
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
                wait_runtime.persist_result(args.log_file, result)
            return result, 1
        finally:
            if lock is not None:
                lock.close()
                self._update_record(record, lock_status="released")

    async def _activate_goal_wait(
        self,
        args: argparse.Namespace,
        cancel_event: asyncio.Event,
        deadline_at: float,
    ) -> str:
        wait_runtime.persist_result(
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
            phase = wait_runtime.goal_wait_phase(args)
            if phase == "active":
                return "active"
            if phase == "invalid":
                return "cancelled"
            remaining = deadline_at - time.time()
            if remaining <= 0:
                return "timeout"
            if await wait_for_cancel(cancel_event, min(args.activation_interval, remaining)):
                return "cancelled"

    async def _execute_program(
        self,
        args: argparse.Namespace,
        command: list[str],
        record: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, object]:
        remaining = float(record["deadline_at"]) - time.time()
        if remaining <= 0:
            return {"event": "timeout", "exit_code": None, "stdout": "", "stderr": ""}
        def started(pid: int) -> None:
            self._update_record(record, phase="querying", query_status="verified", query_pid=pid)

        task = asyncio.create_task(run_program(command, remaining, record["cwd"], started))
        try:
            while not task.done():
                if cancel_event.is_set() or not wait_runtime.watch_is_current(args):
                    return {"event": "cancelled", "exit_code": None, "stdout": "", "stderr": ""}
                await asyncio.wait({task}, timeout=0.25)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
        max_attempts, timeout = wait_runtime.notification_limits(args)
        previous_attempts = int(result.get("notification_attempts", 0))
        for attempt in range(previous_attempts + 1, max_attempts + 1):
            if cancel_event.is_set() or not wait_runtime.watch_is_current(args):
                result["notification"] = "cancelled"
                return wait_runtime.EXIT_ACTIVATION_CANCELLED
            result.update(notification="attempting", notification_attempts=attempt)
            self._persist_result(args, record, result)
            message = wait_runtime.resume_instruction(args)
            try:
                remote = args.remote
                if args.client == "codex":
                    remote = wait_runtime.resolve_codex_remote(args.remote)
                command = wait_runtime.notification_command(
                    args.client,
                    args.thread,
                    message,
                    remote,
                    args.resume_args,
                )
            except wait_runtime.NotificationUnavailable as exc:
                result.update(notification="notification_unavailable", notification_stderr=str(exc))
                self._persist_result(args, record, result)
                return wait_runtime.EXIT_NOTIFY_FAILED
            outcome = await run_program(command, timeout, record["cwd"])
            if outcome["event"] == "start_failed":
                result.update(notification="failed", notification_stderr=outcome["error"])
                self._persist_result(args, record, result)
                return wait_runtime.EXIT_NOTIFY_FAILED
            result.update(
                notification_exit_code=outcome["exit_code"],
                notification_stdout=outcome["stdout"],
                notification_stderr=outcome["stderr"],
            )
            if outcome.get("output_truncated"):
                result["notification_output_truncated"] = True
            if outcome["event"] == "timeout":
                failed = "unconfirmed"
            elif outcome["exit_code"] == 0:
                result.update(notification=wait_runtime.notification_success(args.client), delivered_at=time.time())
                self._persist_result(args, record, result)
                return None
            elif args.client == "codex" and await self._codex_channel_lost(args, outcome["stderr"]):
                result["notification"] = "notification_unavailable"
                self._persist_result(args, record, result)
                return wait_runtime.EXIT_NOTIFY_FAILED
            else:
                failed = "failed"
            exhausted = attempt == max_attempts
            result["notification"] = failed if exhausted else f"{failed}_retrying"
            self._persist_result(args, record, result)
            if exhausted:
                return wait_runtime.EXIT_NOTIFY_FAILED
            delay = min(args.notification_retry_interval * 2 ** min(attempt - 1, 10), 300.0)
            await wait_for_cancel(cancel_event, delay)
        return wait_runtime.EXIT_NOTIFY_FAILED

    async def _codex_channel_lost(self, args: argparse.Namespace, stderr: str) -> bool:
        """A dead endpoint must not be retried; re-check it when stderr is inconclusive."""
        if wait_runtime.notification_failure_is_unavailable(stderr):
            return True
        try:
            await asyncio.to_thread(wait_runtime.preflight_codex_remote, args.remote)
        except wait_runtime.NotificationUnavailable:
            return True
        return False

    async def _await_goal_wake(
        self,
        args: argparse.Namespace,
        cancel_event: asyncio.Event,
        deadline_at: float,
    ) -> None:
        while wait_runtime.goal_wait_is_current(args):
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
        daemon = WaitDaemon(registry_path, socket_path)
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
