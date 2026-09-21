"""Ownership, persistence, and client delivery support for waitd."""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import math
import os
import secrets
import socket
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path


class StateError(ValueError):
    """Raised when a durable goal or loop state file is missing, invalid, or unwritable."""


class NotificationUnavailable(ValueError):
    """Raised when a notification endpoint cannot accept wake messages."""


EXIT_NOTIFY_FAILED = 70
EXIT_ALREADY_WATCHING = 75
EXIT_ACTIVATION_CANCELLED = 76
EXIT_TIMEOUT = 124
CLIENTS = {"claude", "codewiz", "codex", "copilot", "cursor"}
DEFAULT_WAIT_TIMEOUT = 3600.0
MAX_EVENT_NOTE_LENGTH = 240
LEGACY_QUERY_OPTIONS = {"--ready", "--terminal", "--interval", "--query-timeout"}


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


def concise_event_note(value: str) -> str:
    note = value.strip()
    if not note:
        raise argparse.ArgumentTypeError("must not be blank")
    if len(note) > MAX_EVENT_NOTE_LENGTH:
        raise argparse.ArgumentTypeError(f"must be at most {MAX_EVENT_NOTE_LENGTH} characters")
    if "\r" in note or "\n" in note:
        raise argparse.ArgumentTypeError("must be a single line")
    return note


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


def validate_job_argv(argv: Sequence[str]) -> None:
    try:
        boundary = argv.index("--")
    except ValueError as exc:
        raise ValueError("watcher options must be followed by '--' and a waiting program") from exc
    misplaced = sorted({item.split("=", 1)[0] for item in argv[:boundary]} & LEGACY_QUERY_OPTIONS)
    if misplaced:
        raise ValueError(
            f"{', '.join(misplaced)} are not watcher options; the legacy status-matching CLI was removed. "
            "Put status logic in the waiting program after '--'."
        )
    if boundary == len(argv) - 1:
        raise ValueError("a waiting program is required after '--'")


def notification_command(
    client: str,
    session: str,
    message: str,
    remote: str | None,
    resume_args: Sequence[str] = (),
) -> list[str]:
    if client == "codex":
        if not remote:
            raise NotificationUnavailable("notification_unavailable: no Codex app-server endpoint; pass --remote")
        return ["codex", "queue", "--remote", remote, "--thread", session, *resume_args, "--message", message]
    if client == "codewiz":
        return ["codewiz", "run", "--session", session, *resume_args, message]
    if client == "cursor":
        return ["cursor-agent", "--print", f"--resume={session}", *resume_args, message]
    if client == "claude":
        raise ValueError("Claude requires a native background task running waitctl follow, not CLI resume")
    if client == "copilot":
        return ["copilot", f"--resume={session}", *resume_args, "--prompt", message]
    raise ValueError(f"unsupported client: {client}")


def notification_success(client: str) -> str:
    return "queued" if client == "codex" else "completed"


def codex_control_socket() -> Path:
    """Default Codex app-server control socket, honoring CODEX_HOME."""
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return root / "app-server-control" / "app-server-control.sock"


def resolve_codex_remote(remote: str | None) -> str:
    """Normalize a Codex --remote value; empty and bare unix:// mean the control socket."""
    if not remote or remote == "unix://":
        return f"unix://{codex_control_socket()}"
    if remote.startswith("unix://"):
        path = Path(remote.removeprefix("unix://")).expanduser()
        if not path.is_absolute():
            raise NotificationUnavailable(
                f"notification_unavailable: unix socket path must be absolute: {remote}"
            )
        return f"unix://{path}"
    if remote.startswith(("ws://", "wss://")):
        return remote
    raise NotificationUnavailable(f"notification_unavailable: unsupported Codex --remote: {remote}")


def preflight_codex_remote(remote: str | None, timeout: float = 1.0) -> str:
    """Return a delivery-ready endpoint or raise NotificationUnavailable.

    Unix endpoints must complete a WebSocket upgrade handshake, which tells the
    app-server control socket apart from unrelated sockets such as
    ~/.codex/ipc/ipc.sock. Explicit ws:// and wss:// endpoints are returned as
    configured without a probe.
    """
    endpoint = resolve_codex_remote(remote)
    if not endpoint.startswith("unix://"):
        return endpoint
    path = Path(endpoint.removeprefix("unix://"))
    if not path.exists():
        raise NotificationUnavailable(
            f"notification_unavailable: Codex app-server socket not found: {path}; "
            "pass --remote or enable the app-server control socket"
        )
    request = (
        "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {base64.b64encode(secrets.token_bytes(16)).decode()}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode()
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(timeout)
            connection.connect(os.fspath(path))
            connection.sendall(request)
            response = connection.recv(4096)
    except OSError as exc:
        raise NotificationUnavailable(f"notification_unavailable: cannot reach {path}: {exc}") from exc
    status = response.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        detail = status.decode("utf-8", errors="replace").strip() or "no HTTP response"
        raise NotificationUnavailable(
            f"notification_unavailable: {path} is not a Codex app-server endpoint ({detail})"
        )
    return endpoint


CONNECTION_FAILURE_MARKERS = (
    "failed to connect",
    "connection refused",
    "no such file or directory",
    "handshake",
)


def notification_failure_is_unavailable(stderr: str) -> bool:
    """Whether codex queue stderr describes an unreachable endpoint, not a queue rejection."""
    return any(marker in stderr.casefold() for marker in CONNECTION_FAILURE_MARKERS)


def notification_limits(args: argparse.Namespace) -> tuple[int, float]:
    """Resolve the shared CLI and service delivery policy."""
    attempts = args.max_notification_attempts
    timeout = args.notification_timeout
    if attempts is None:
        attempts = 12 if args.client == "codex" else 1
    if timeout is None:
        timeout = 60.0 if args.client == "codex" else 3600.0
    return attempts, timeout


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
    from wait_loop import goal_is_open

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
        and goal_is_open(state)
    )


def watch_is_current(args: argparse.Namespace) -> bool:
    """Integrated waits must still own their persisted watch."""
    if getattr(args, "goal_state", None):
        return goal_wait_is_current(args)
    if getattr(args, "loop_state", None):
        return loop_wait_is_current(args)
    return True


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


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Block until an exclusive advisory lock on path's ``.lock`` sibling is held."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, payload)
    path.chmod(0o600)


def persist_result(path: Path | None, result: dict[str, object]) -> None:
    if path:
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
        atomic_write_text(path, payload)


def add_runtime_arguments(result: argparse.ArgumentParser) -> None:
    """Shared ownership and delivery options, independent of status matching."""
    result.add_argument("--client", choices=sorted(CLIENTS), default="codex")
    result.add_argument(
        "--session",
        "--thread",
        dest="thread",
        help="Existing agent session ID to resume; --thread is a compatibility alias",
    )
    result.add_argument(
        "--remote",
        help="Codex app-server endpoint; defaults to the app-server control socket when omitted",
    )
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
    result.add_argument(
        "--event-note",
        type=concise_event_note,
        help="Short agent-authored instruction for what to do after the event",
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
        "--wake-ack-timeout",
        type=positive_number,
        default=60.0,
        help="Maximum seconds to retain a goal watcher lock after successful notification",
    )
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


def resume_instruction(args: argparse.Namespace) -> str:
    """Build the wake command with its agent-authored note."""
    if args.goal_state:
        command = f"$wait-goal resume {args.goal_state}"
        fields = [f"node={args.goal_node}", f"event_id={args.event_id}", f"log_file={args.log_file}"]
    elif args.loop_state:
        command = f"$wait-loop resume {args.loop_state}"
        fields = [f"event_id={args.event_id}", f"log_file={args.log_file}"]
    else:
        command = f"$wait resume {args.log_file}"
        fields = [f"event_id={args.event_id}"]
    if note := getattr(args, "event_note", None):
        fields.append(f"event_note={json.dumps(note, ensure_ascii=False)}")
    return "; ".join([command, *fields])


def job_parser() -> argparse.ArgumentParser:
    """Specification for a managed waiting program, without business-state parsing."""
    result = argparse.ArgumentParser(description=job_parser.__doc__, allow_abbrev=False)
    result.add_argument("--label", required=True)
    result.add_argument("--timeout", type=positive_number, default=DEFAULT_WAIT_TIMEOUT)
    add_runtime_arguments(result)
    result.add_argument("command", nargs=argparse.REMAINDER, help="Waiting program after --")
    return result


def parse_job_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    validate_job_argv(argv)
    command_parser = job_parser()
    args = command_parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        command_parser.error("a waiting program is required after --")
    paths = {name: getattr(args, name) for name in ("goal_state", "loop_state", "startup_file", "log_file", "lock_file")}
    validate_distinct_paths(command_parser, **paths)
    if args.event_id is not None and (
        not args.event_id.strip() or len(args.event_id) > 128 or any(c.isspace() for c in args.event_id)
    ):
        command_parser.error("--event-id must be a non-empty identifier of at most 128 characters")
    handshake = (args.goal_state, args.goal_node, args.startup_file)
    if any(handshake) and not all(handshake):
        command_parser.error("--goal-state, --goal-node, and --startup-file must be provided together")
    if args.goal_state and args.loop_state:
        command_parser.error("--goal-state and --loop-state are mutually exclusive")
    if (args.goal_state or args.loop_state) and not all((args.thread, args.event_id, args.lock_file, args.log_file)):
        command_parser.error("integrated waits require session, event ID, lock and log paths")
    return args, command
