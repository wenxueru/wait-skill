from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from argparse import ArgumentTypeError
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import wait_runtime  # noqa: E402


class RuntimeTest(unittest.TestCase):
    def test_event_note_must_be_concise_and_single_line(self) -> None:
        self.assertEqual(wait_runtime.concise_event_note("  Recheck deployment  "), "Recheck deployment")
        for value in ("", "line one\nline two", "x" * 241):
            with self.subTest(value=value[:20]), self.assertRaises(ArgumentTypeError):
                wait_runtime.concise_event_note(value)

    def test_notification_limits_preserve_defaults_and_overrides(self) -> None:
        for client in sorted(wait_runtime.CLIENTS):
            with self.subTest(client=client):
                args = Namespace(client=client, max_notification_attempts=None, notification_timeout=None)
                expected = (12, 60.0) if client == "codex" else (1, 3600.0)
                self.assertEqual(wait_runtime.notification_limits(args), expected)
                args.max_notification_attempts = 3
                self.assertEqual(wait_runtime.notification_limits(args), (3, expected[1]))
                args.notification_timeout = 90.0
                self.assertEqual(wait_runtime.notification_limits(args), (3, 90.0))

    def test_notification_commands_cover_supported_clients(self) -> None:
        expected = {
            "codex": ["codex", "queue", "--remote", "unix://", "--thread", "session-1", "--message", "resume"],
            "codewiz": ["codewiz", "run", "--session", "session-1", "resume"],
            "cursor": ["cursor-agent", "--print", "--resume=session-1", "resume"],
            "copilot": ["copilot", "--resume=session-1", "--prompt", "resume"],
        }
        for client, command in expected.items():
            with self.subTest(client=client):
                self.assertEqual(
                    wait_runtime.notification_command(client, "session-1", "resume", "unix://"),
                    command,
                )

        self.assertEqual(
            wait_runtime.notification_command(
                "cursor",
                "session-1",
                "resume",
                "unix://",
                ["--force"],
            ),
            ["cursor-agent", "--print", "--resume=session-1", "--force", "resume"],
        )

    def test_codex_remote_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                default = f"unix://{Path(home) / 'app-server-control' / 'app-server-control.sock'}"
                self.assertEqual(wait_runtime.resolve_codex_remote(None), default)
                self.assertEqual(wait_runtime.resolve_codex_remote("unix://"), default)
        self.assertEqual(
            wait_runtime.resolve_codex_remote("unix:///tmp/codex-control.sock"),
            "unix:///tmp/codex-control.sock",
        )
        self.assertEqual(wait_runtime.resolve_codex_remote("wss://localhost:8000"), "wss://localhost:8000")
        for remote in ("http://localhost:8000", "unix://relative.sock"):
            with self.subTest(remote=remote), self.assertRaisesRegex(
                wait_runtime.NotificationUnavailable, "notification_unavailable"
            ):
                wait_runtime.resolve_codex_remote(remote)

    def test_codex_preflight_requires_a_reachable_control_socket(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                with self.assertRaisesRegex(wait_runtime.NotificationUnavailable, "socket not found"):
                    wait_runtime.preflight_codex_remote(None)

    def test_codex_preflight_requires_a_websocket_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            path.touch()
            for response, available in (
                (b"HTTP/1.1 101 Switching Protocols\r\n\r\n", True),
                (b"HTTP/1.1 400 Bad Request\r\n\r\n", False),
                (b"", False),
            ):
                with self.subTest(response=response):
                    connection = mock.MagicMock()
                    connection.__enter__.return_value = connection
                    connection.recv.return_value = response
                    with mock.patch.object(wait_runtime.socket, "socket", return_value=connection):
                        endpoint = f"unix://{path}"
                        if available:
                            self.assertEqual(wait_runtime.preflight_codex_remote(endpoint), endpoint)
                        else:
                            with self.assertRaisesRegex(
                                wait_runtime.NotificationUnavailable, "notification_unavailable"
                            ):
                                wait_runtime.preflight_codex_remote(endpoint)

    def test_notification_connection_failures_are_unavailable(self) -> None:
        for stderr in (
            "Failed to connect to remote app server",
            "Connection refused (os error 61)",
            "No such file or directory (os error 2)",
            "WebSocket handshake not finished",
        ):
            with self.subTest(stderr=stderr):
                self.assertTrue(wait_runtime.notification_failure_is_unavailable(stderr))
        self.assertFalse(wait_runtime.notification_failure_is_unavailable("thread not found"))

    def test_loop_watch_must_still_own_the_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "loop.json"
            state.write_text(
                json.dumps({
                    "status": "active",
                    "phase": "waiting",
                    "watch_id": "watch-1",
                    "client": "claude",
                    "session": "session-1",
                }),
                encoding="utf-8",
            )
            args = Namespace(
                loop_state=state,
                event_id="watch-1",
                client="claude",
                thread="session-1",
            )
            self.assertTrue(wait_runtime.loop_wait_is_current(args))

            args.event_id = "stale"
            self.assertFalse(wait_runtime.loop_wait_is_current(args))

    def test_missing_or_corrupt_goal_state_invalidates_watch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "goal.json"
            args = Namespace(goal_state=state, goal_node="deploy", event_id="watch-1")
            self.assertFalse(wait_runtime.goal_wait_is_current(args))

            state.write_text("{", encoding="utf-8")
            self.assertFalse(wait_runtime.goal_wait_is_current(args))

    def test_goal_watch_must_match_persisted_client_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "goal.json"
            state.write_text(
                json.dumps({
                    "nodes": {
                        "deploy": {
                            "status": "waiting",
                            "wait": {
                                "phase": "active",
                                "watch_id": "watch-1",
                                "client": "claude",
                                "thread": "expected-thread",
                            },
                        }
                    }
                }),
                encoding="utf-8",
            )
            args = Namespace(
                goal_state=state,
                goal_node="deploy",
                event_id="watch-1",
                client="codex",
                thread="expected-thread",
            )
            self.assertFalse(wait_runtime.goal_wait_is_current(args))

            args.client = "claude"
            args.thread = "wrong-thread"
            self.assertFalse(wait_runtime.goal_wait_is_current(args))
