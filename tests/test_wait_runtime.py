from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import ArgumentTypeError
from argparse import Namespace
from pathlib import Path

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
