#!/usr/bin/env python3
"""Wait for command-reported state and durably resume an agent session."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import selectors
import signal
import string
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import ExitStack, suppress
from pathlib import Path

EXIT_READY = 0
EXIT_TERMINAL = 2
EXIT_QUERY_FAILED = 3
EXIT_NOTIFY_FAILED = 70
EXIT_ALREADY_WATCHING = 75
EXIT_ACTIVATION_CANCELLED = 76
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130
MESSAGE_FIELDS = {
    "label",
    "event",
    "event_id",
    "status",
    "query_failures",
    "elapsed_seconds",
    "notification",
    "notification_attempts",
}
MAX_QUERY_OUTPUT_BYTES = 64 * 1024
CLIENTS = {"claude", "codewiz", "codex", "copilot", "cursor"}
DEFAULT_WAIT_TIMEOUT = 86400.0


class QueryConfigurationError(ValueError):
    """A safe-to-report query contract error that retries cannot fix."""


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def canonical_path(path: Path) -> Path:
    return Path(os.path.realpath(absolute_path(path)).casefold())


def validate_distinct_paths(
    command_parser: argparse.ArgumentParser,
    **paths: Path | None,
) -> None:
    seen: dict[Path, str] = {}
    for name, path in paths.items():
        if path is None:
            continue
        canonical = canonical_path(path)
        if previous := seen.get(canonical):
            current_option = name.replace("_", "-")
            previous_option = previous.replace("_", "-")
            command_parser.error(f"--{current_option} must differ from --{previous_option}")
        seen[canonical] = name


def extract_status(output: str, json_path: str | None = None) -> str:
    if json_path:
        value: object = json.loads(output)
        for key in json_path.split("."):
            if not key or not isinstance(value, dict) or key not in value:
                raise ValueError(f"JSON path not found: {json_path}")
            value = value[key]
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("status must be a scalar JSON value")
        if isinstance(value, bool):
            status = str(value).lower()
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("status must be a finite JSON scalar")
        else:
            status = str(value)
    else:
        try:
            structured = json.loads(output)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(structured, (dict, list)):
                raise QueryConfigurationError("query produced structured JSON; specify --json-path")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            raise ValueError("query produced no status")
        status = lines[-1]

    if len(status) > 128 or any(character in status for character in "\r\n{}[]"):
        raise ValueError("status must be a short, single scalar value")
    return status


def query_status(command: Sequence[str], query_timeout: float, json_path: str | None) -> str:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    output = bytearray()
    deadline = time.monotonic() + query_timeout
    assert process.stdout is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, query_timeout)
                if not selector.select(remaining):
                    raise subprocess.TimeoutExpired(command, query_timeout)
                read_size = min(8192, MAX_QUERY_OUTPUT_BYTES + 1 - len(output))
                chunk = os.read(process.stdout.fileno(), read_size)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_QUERY_OUTPUT_BYTES:
                    raise ValueError(f"query output exceeds {MAX_QUERY_OUTPUT_BYTES} bytes")
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except BaseException:
        terminate_process_group(process)
        raise
    finally:
        process.stdout.close()
    if returncode:
        raise RuntimeError(f"query exited with status {returncode}")
    return extract_status(output.decode("utf-8"), json_path)


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out query and every descendant in its process group."""
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.wait()


class StatusWaiter:
    """Run one bounded status-wait lifecycle."""

    def __init__(
        self,
        query: Callable[[float | None], str],
        ready: set[str],
        terminal: set[str],
        *,
        interval: float,
        timeout: float | None,
        max_consecutive_failures: int,
        is_active: Callable[[], bool] | None = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.query = query
        self.ready = ready
        self.terminal = terminal
        self.interval = interval
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.is_active = is_active
        self.now = now
        self.sleep = sleep
        self.started: float
        self.deadline: float
        self.status: str | None
        self.failures: int
        self.consecutive_failures: int
        self.error: str | None

    def run(self) -> tuple[dict[str, object], int]:
        self.started = self.now()
        self.deadline = self.started + self.timeout if self.timeout is not None else math.inf
        self.status = None
        self.failures = 0
        self.consecutive_failures = 0
        self.error = None

        while True:
            if self.is_active is not None and not self.is_active():
                return self._result("cancelled", EXIT_ACTIVATION_CANCELLED)
            remaining = None if self.timeout is None else self.deadline - self.now()
            if remaining is not None and remaining <= 0:
                return self._result("timeout", EXIT_TIMEOUT)
            try:
                candidate = self.query(remaining)
            except QueryConfigurationError as exc:
                self.failures += 1
                self.error = str(exc)
                return self._result("query_failed", EXIT_QUERY_FAILED)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                outcome = self._record_failure()
            else:
                outcome = self._record_status(candidate)
            if outcome is not None:
                return self._result(*outcome)
            self.sleep(max(0.0, min(self.interval, self.deadline - self.now())))

    def _record_failure(self) -> tuple[str, int] | None:
        if self.now() >= self.deadline:
            return "timeout", EXIT_TIMEOUT
        self.failures += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            return "query_failed", EXIT_QUERY_FAILED
        return None

    def _record_status(self, status: str) -> tuple[str, int] | None:
        if self.now() >= self.deadline:
            return "timeout", EXIT_TIMEOUT
        self.status = status
        self.consecutive_failures = 0
        if status in self.ready:
            return "ready", EXIT_READY
        if status in self.terminal:
            return "terminal", EXIT_TERMINAL
        return None

    def _result(self, event: str, code: int) -> tuple[dict[str, object], int]:
        result: dict[str, object] = {
            "event": event,
            "status": self.status,
            "query_failures": self.failures,
            "elapsed_seconds": round(self.now() - self.started, 3),
        }
        if self.error:
            result["error"] = self.error
        return result, code


def notification_command(
    client: str,
    session: str,
    message: str,
    remote: str,
    resume_args: Sequence[str] = (),
) -> list[str]:
    if client == "codex":
        return ["codex", "queue", "--remote", remote, "--thread", session, *resume_args, "--message", message]
    if client == "codewiz":
        return ["codewiz", "run", "--session", session, *resume_args, message]
    if client == "cursor":
        return ["cursor-agent", "--print", f"--resume={session}", *resume_args, message]
    if client == "claude":
        return ["claude", "--print", "--resume", session, *resume_args, message]
    if client == "copilot":
        return ["copilot", f"--resume={session}", *resume_args, "--prompt", message]
    raise ValueError(f"unsupported client: {client}")


def notify_session(
    client: str,
    session: str,
    remote: str,
    label: str,
    result: dict[str, object],
    template: str,
    timeout: float = 60.0,
    resume_args: Sequence[str] = (),
) -> None:
    fields = {**result, "label": label}
    message = template.format(**fields)
    subprocess.run(
        notification_command(client, session, message, remote, resume_args),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def deliver_notification(
    args: argparse.Namespace,
    result: dict[str, object],
) -> int | None:
    """Persist delivery progress and retry with one stable event ID."""
    max_attempts = args.max_notification_attempts
    if max_attempts is None:
        max_attempts = 12 if args.client == "codex" else 1
    notification_timeout = args.notification_timeout
    if notification_timeout is None:
        notification_timeout = 60.0 if args.client == "codex" else 3600.0
    attempts = 0
    result["notification"] = "pending"
    result["notification_attempts"] = attempts
    persist_result(args.log_file, result)
    while True:
        if not watch_is_current(args):
            result["notification"] = "cancelled"
            persist_result(args.log_file, result)
            return EXIT_ACTIVATION_CANCELLED
        attempts += 1
        result["notification_attempts"] = attempts
        result["notification"] = "attempting"
        persist_result(args.log_file, result)
        try:
            notify_session(
                args.client,
                args.thread,
                args.remote,
                args.label,
                result,
                args.message_template,
                timeout=notification_timeout,
                resume_args=args.resume_args,
            )
        except KeyboardInterrupt:
            result["notification"] = "interrupted"
            persist_result(args.log_file, result)
            return EXIT_INTERRUPTED
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            exhausted = attempts >= max_attempts
            ambiguous = isinstance(exc, subprocess.TimeoutExpired)
            if exhausted:
                result["notification"] = "unconfirmed" if ambiguous else "failed"
            else:
                result["notification"] = "unconfirmed_retrying" if ambiguous else "retrying"
            persist_result(args.log_file, result)
            if exhausted:
                return EXIT_NOTIFY_FAILED
            try:
                delay = min(
                    args.notification_retry_interval * 2 ** min(attempts - 1, 10),
                    300.0,
                )
                time.sleep(delay)
            except KeyboardInterrupt:
                result["notification"] = "interrupted"
                persist_result(args.log_file, result)
                return EXIT_INTERRUPTED
        else:
            result["notification"] = "queued"
            persist_result(args.log_file, result)
            return None


def goal_wait_phase(args: argparse.Namespace) -> str:
    """Return this watch's persisted phase or invalid when state is unavailable."""
    try:
        state = json.loads(args.goal_state.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(state, dict):
        return "invalid"
    nodes = state.get("nodes")
    if not isinstance(nodes, dict):
        return "invalid"
    node = nodes.get(args.goal_node)
    if not isinstance(node, dict):
        return "invalid"
    wait = node.get("wait")
    if (
        not isinstance(wait, dict)
        or wait.get("watch_id") != args.event_id
        or wait.get("client", "codex") != getattr(args, "client", "codex")
        or wait.get("thread") != args.thread
    ):
        return "invalid"
    phase = wait.get("phase")
    if node.get("status") == "running" and phase == "prepared":
        return "prepared"
    if node.get("status") == "waiting" and phase == "active":
        return "active"
    return "invalid"


def goal_wait_is_current(args: argparse.Namespace) -> bool:
    """Return whether this exact watch remains active in durable goal state."""
    return goal_wait_phase(args) == "active"


def loop_wait_is_current(args: argparse.Namespace) -> bool:
    try:
        state = json.loads(args.loop_state.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(state, dict)
        and state.get("status") == "active"
        and state.get("phase") == "waiting"
        and state.get("watch_id") == args.event_id
        and state.get("client", "codex") == args.client
        and state.get("session") == args.thread
    )


def watch_is_current(args: argparse.Namespace) -> bool:
    """Integrated waits must still own their persisted watch."""
    if getattr(args, "goal_state", None):
        return goal_wait_is_current(args)
    if getattr(args, "loop_state", None):
        return loop_wait_is_current(args)
    return True


def wait_for_goal_activation(args: argparse.Namespace) -> str:
    """Wait for activation without allowing a prepared watcher to live forever."""
    deadline = time.monotonic() + args.activation_timeout
    while True:
        phase = goal_wait_phase(args)
        if phase == "active":
            return "active"
        if phase == "invalid":
            return "cancelled"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        time.sleep(min(args.activation_interval, remaining))


def atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def persist_result(path: Path | None, result: dict[str, object]) -> None:
    if path:
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
        atomic_write_text(path, payload)


def write_result(path: Path | None, result: dict[str, object]) -> None:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if path:
        atomic_write_text(path, payload + "\n")
    print(payload, flush=True)


def validate_message_template(
    command_parser: argparse.ArgumentParser,
    template: str,
    log_file: Path | None,
    goal_state: Path | None = None,
    goal_node: str | None = None,
    loop_state: Path | None = None,
) -> None:
    try:
        fields = {field_name for _, field_name, _, _ in string.Formatter().parse(template) if field_name}
    except ValueError as exc:
        command_parser.error(f"invalid --message-template: {exc}")
    required = {"event_id", "event", "status"}
    if not required <= fields:
        command_parser.error("--message-template must include {event_id}, {event}, and {status}")
    unknown = fields - MESSAGE_FIELDS
    if unknown:
        command_parser.error(f"unknown --message-template fields: {sorted(unknown)}")
    try:
        template.format(
            label="label",
            event="ready",
            event_id="event-id",
            status="status",
            query_failures=0,
            elapsed_seconds=0.0,
            notification="pending",
            notification_attempts=0,
        )
    except (IndexError, KeyError, ValueError) as exc:
        command_parser.error(f"invalid --message-template: {exc}")
    resume_directives = re.findall(r"[$/](wait(?:-goal|-loop)?)\s+resume\s+(.+?)\s*(?=;|$)", template)
    if len(resume_directives) != 1:
        command_parser.error("--message-template must contain exactly one wait, wait-loop, or wait-goal resume directive")
    resume_skill, resume_target = resume_directives[0]
    if goal_state or loop_state:
        watcher_logs = re.findall(r"(?<![A-Za-z0-9._-])watcher_log=(.+?)\s*(?=;|$)", template)
    if goal_state:
        if resume_skill != "wait-goal" or resume_target != str(goal_state):
            command_parser.error("--message-template must resume the active goal state")
        nodes = re.findall(r"(?<![A-Za-z0-9._-])node=(.+?)\s*(?=;|$)", template)
        if watcher_logs != [str(log_file)]:
            command_parser.error("--message-template must contain exactly one active watcher_log binding")
        if nodes != [goal_node]:
            command_parser.error("--message-template must contain exactly one active goal node binding")
    elif loop_state:
        if resume_skill != "wait-loop" or resume_target != str(loop_state):
            command_parser.error("--message-template must resume the active loop state")
        if watcher_logs != [str(log_file)]:
            command_parser.error("--message-template must contain exactly one active watcher_log binding")
    elif log_file:
        if resume_skill == "wait" and resume_target == str(log_file):
            return
        command_parser.error("--message-template must bind the active wait watcher log")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--label", required=True, help="Safe human-readable object label")
    result.add_argument("--ready", action="append", required=True, help="Exact ready status; repeatable")
    result.add_argument("--terminal", action="append", default=[], help="Exact terminal status; repeatable")
    result.add_argument("--json-path", help="Dot-separated path to a scalar status in JSON stdout")
    result.add_argument("--interval", type=positive_number, default=300.0)
    result.add_argument("--query-timeout", type=positive_number, default=30.0)
    result.add_argument(
        "--timeout",
        type=positive_number,
        default=DEFAULT_WAIT_TIMEOUT,
        help="Overall wait limit in seconds; defaults to 86400 (24 hours)",
    )
    result.add_argument(
        "--max-consecutive-failures",
        type=positive_int,
        default=12,
        help="Wake after this many consecutive query failures; must be positive",
    )
    result.add_argument("--client", choices=sorted(CLIENTS), default="codex")
    result.add_argument(
        "--session",
        "--thread",
        dest="thread",
        help="Existing agent session ID to resume; --thread is a compatibility alias",
    )
    result.add_argument("--remote", default="unix://", help="Codex app-server endpoint")
    result.add_argument(
        "--resume-arg",
        dest="resume_args",
        action="append",
        default=[],
        help="Explicit client resume argument; repeat as needed and use --resume-arg=VALUE for flags",
    )
    result.add_argument("--lock-file", type=Path, help="Reject another watcher holding this lock")
    result.add_argument("--log-file", type=Path, help="Write the final JSON result here")
    result.add_argument(
        "--event-id",
        help="Stable event ID; pass the prepared wait-goal watch ID when integrating",
    )
    result.add_argument("--goal-state", type=Path, help="Goal state used for startup activation")
    result.add_argument("--goal-node", help="External goal node used for startup activation")
    result.add_argument("--loop-state", type=Path, help="Loop state that owns this timer watch")
    result.add_argument(
        "--startup-file",
        type=Path,
        help="Write watcher startup confirmation before waiting for goal activation",
    )
    result.add_argument("--activation-interval", type=positive_number, default=0.25)
    result.add_argument("--activation-timeout", type=positive_number, default=60.0)
    result.add_argument(
        "--notification-timeout",
        type=positive_number,
        help="Maximum seconds per delivery attempt; defaults to 60 for Codex and 3600 otherwise",
    )
    result.add_argument(
        "--notification-retry-interval",
        type=positive_number,
        default=30.0,
        help="Seconds between notification retries",
    )
    result.add_argument(
        "--max-notification-attempts",
        type=positive_int,
        default=None,
        help="Positive delivery-attempt limit; defaults to 12 for Codex and one otherwise",
    )
    result.add_argument(
        "--message-template",
        help=(
            "Required with --session; format string using label, event, event_id, status, query_failures, and elapsed_seconds"
        ),
    )
    result.add_argument("command", nargs=argparse.REMAINDER, help="Read-only query command after --")
    return result


def run_wait(args: argparse.Namespace, command: Sequence[str]) -> int:
    started = time.monotonic()
    try:
        result, code = StatusWaiter(
            lambda remaining: query_status(
                command,
                min(args.query_timeout, remaining) if remaining is not None else args.query_timeout,
                args.json_path,
            ),
            set(args.ready),
            set(args.terminal),
            interval=args.interval,
            timeout=args.timeout,
            max_consecutive_failures=args.max_consecutive_failures,
            is_active=lambda: watch_is_current(args),
        ).run()
    except KeyboardInterrupt:
        result, code = (
            {
                "event": "interrupted",
                "status": None,
                "query_failures": 0,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            },
            EXIT_INTERRUPTED,
        )

    result["label"] = args.label
    result["event_id"] = args.event_id or uuid.uuid4().hex
    if code != EXIT_INTERRUPTED and not watch_is_current(args):
        result["event"] = "cancelled"
        code = EXIT_ACTIVATION_CANCELLED
    persist_result(args.log_file, result)
    if args.thread and code not in {EXIT_INTERRUPTED, EXIT_ACTIVATION_CANCELLED}:
        delivery_code = deliver_notification(args, result)
        if delivery_code is not None:
            code = delivery_code
    write_result(args.log_file, result)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        command_parser.error("a query command is required after --")
    if args.goal_state or args.loop_state:
        for field in ("goal_state", "loop_state", "startup_file", "log_file", "lock_file"):
            path = getattr(args, field)
            if path is not None:
                setattr(args, field, absolute_path(path))
    validate_distinct_paths(
        command_parser,
        goal_state=args.goal_state,
        loop_state=args.loop_state,
        startup_file=args.startup_file,
        log_file=args.log_file,
        lock_file=args.lock_file,
    )
    if args.thread and not args.log_file:
        command_parser.error("--log-file is required with --session")
    if args.thread and not args.lock_file:
        command_parser.error("--lock-file is required with --session")
    if args.thread and not args.message_template:
        command_parser.error("--message-template is required with --session")
    overlap = set(args.ready) & set(args.terminal)
    if overlap:
        command_parser.error(f"ready and terminal statuses must be disjoint: {sorted(overlap)}")
    if args.message_template:
        validate_message_template(
            command_parser,
            args.message_template,
            args.log_file,
            args.goal_state,
            args.goal_node,
            args.loop_state,
        )
    if args.event_id is not None and (
        not args.event_id.strip() or len(args.event_id) > 128 or any(character.isspace() for character in args.event_id)
    ):
        command_parser.error("--event-id must be a non-empty identifier of at most 128 characters")
    handshake = (args.goal_state, args.goal_node, args.startup_file)
    if any(handshake) and not all(handshake):
        command_parser.error("--goal-state, --goal-node, and --startup-file must be provided together")
    if args.goal_state and not args.event_id:
        command_parser.error("--event-id is required with goal activation")
    if args.goal_state and not args.thread:
        command_parser.error("--session is required with goal activation")
    if args.goal_state and args.loop_state:
        command_parser.error("--goal-state and --loop-state are mutually exclusive")
    if args.loop_state and not args.event_id:
        command_parser.error("--event-id is required with --loop-state")
    if args.loop_state and not args.thread:
        command_parser.error("--session is required with --loop-state")

    with ExitStack() as resources:
        if args.lock_file:
            args.lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock = resources.enter_context(args.lock_file.open("a", encoding="utf-8"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                write_result(
                    None,
                    {
                        "label": args.label,
                        "event_id": uuid.uuid4().hex,
                        "event": "already_watching",
                        "status": None,
                    },
                )
                return EXIT_ALREADY_WATCHING
        if args.goal_state:
            persist_result(
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
                    "activation_deadline": time.time() + args.activation_timeout,
                },
            )
            try:
                activation = wait_for_goal_activation(args)
            except KeyboardInterrupt:
                write_result(
                    args.log_file,
                    {
                        "label": args.label,
                        "event_id": args.event_id,
                        "event": "interrupted",
                        "status": None,
                    },
                )
                return EXIT_INTERRUPTED
            if activation != "active":
                write_result(
                    args.log_file,
                    {
                        "label": args.label,
                        "event_id": args.event_id,
                        "event": f"activation_{activation}",
                        "status": None,
                    },
                )
                return EXIT_ACTIVATION_CANCELLED
        return run_wait(args, command)


if __name__ == "__main__":
    raise SystemExit(main())
