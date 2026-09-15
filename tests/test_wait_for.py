from __future__ import annotations

import fcntl
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "wait_for.py"
SPEC = importlib.util.spec_from_file_location("wait_for", SCRIPT)
assert SPEC and SPEC.loader
wait_for = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wait_for)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class WaitForTest(unittest.TestCase):
    def test_extracts_plain_and_json_status(self) -> None:
        self.assertEqual(wait_for.extract_status("diagnostic\nRunning\n"), "Running")
        self.assertEqual(
            wait_for.extract_status('{"job":{"status":"Ready"}}', "job.status"),
            "Ready",
        )
        self.assertEqual(wait_for.extract_status('{"ready":true}', "ready"), "true")
        self.assertEqual(wait_for.extract_status('{"ready":false}', "ready"), "false")
        with self.assertRaises(ValueError):
            wait_for.extract_status('{"status":NaN}', "status")
        with self.assertRaisesRegex(ValueError, "specify --json-path"):
            wait_for.extract_status('{"status":"ServiceReady"}')

    def test_query_output_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            wait_for.query_status(
                [sys.executable, "-c", f"print('x' * {wait_for.MAX_QUERY_OUTPUT_BYTES})"],
                5,
                None,
            )

    def test_query_timeout_terminates_descendants(self) -> None:
        commands = (
            "(sleep 0.2; touch {marker}) & sleep 10",
            "(sleep 0.2; touch {marker}) & exit 0",
        )
        for shell_command in commands:
            with self.subTest(shell_command=shell_command), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "leaked"
                command = ["bash", "-c", shell_command.format(marker=marker)]
                with self.assertRaises(subprocess.TimeoutExpired):
                    wait_for.query_status(command, 0.05, None)
                wait_for.time.sleep(0.3)
                self.assertFalse(marker.exists())

    def test_ready_after_transient_failure(self) -> None:
        clock = Clock()
        outcomes = iter([RuntimeError("secret raw response"), "Queued", "Running"])

        def query(_remaining: float | None) -> str:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result, code = wait_for.StatusWaiter(
            query,
            {"Running"},
            {"Failed"},
            interval=5,
            timeout=None,
            max_consecutive_failures=3,
            now=clock.now,
            sleep=clock.sleep,
        ).run()
        self.assertEqual(code, wait_for.EXIT_READY)
        self.assertEqual(result["event"], "ready")
        self.assertEqual(result["query_failures"], 1)
        self.assertNotIn("secret", str(result))

    def test_repeated_query_failures_stop(self) -> None:
        clock = Clock()

        def query(_remaining: float | None) -> str:
            raise RuntimeError("unavailable")

        result, code = wait_for.StatusWaiter(
            query,
            {"Running"},
            set(),
            interval=5,
            timeout=None,
            max_consecutive_failures=2,
            now=clock.now,
            sleep=clock.sleep,
        ).run()
        self.assertEqual(code, wait_for.EXIT_QUERY_FAILED)
        self.assertEqual(result["query_failures"], 2)

    def test_structured_json_without_path_fails_immediately(self) -> None:
        calls = 0

        def query(_remaining: float | None) -> str:
            nonlocal calls
            calls += 1
            return wait_for.extract_status('{"status":"ServiceReady"}')

        result, code = wait_for.StatusWaiter(
            query,
            {"ServiceReady"},
            set(),
            interval=300,
            timeout=3600,
            max_consecutive_failures=12,
        ).run()

        self.assertEqual(code, wait_for.EXIT_QUERY_FAILED)
        self.assertEqual(calls, 1)
        self.assertEqual(result["error"], "query produced structured JSON; specify --json-path")

    def test_result_returned_after_deadline_is_rejected(self) -> None:
        clock = Clock()
        budgets = []

        def query(remaining: float | None) -> str:
            budgets.append(remaining)
            clock.sleep(20)
            return "Running"

        result, code = wait_for.StatusWaiter(
            query,
            {"Running"},
            set(),
            interval=5,
            timeout=1,
            max_consecutive_failures=3,
            now=clock.now,
            sleep=clock.sleep,
        ).run()
        self.assertEqual(code, wait_for.EXIT_TIMEOUT)
        self.assertEqual(result["event"], "timeout")
        self.assertIsNone(result["status"])
        self.assertEqual(budgets, [1])

    def test_interrupt_records_elapsed_time(self) -> None:
        args = wait_for.parser().parse_args(["--label", "demo", "--ready", "Running", "--", "query"])
        output = StringIO()
        with (
            patch.object(wait_for.time, "monotonic", side_effect=[10.0, 12.5]),
            patch.object(wait_for.StatusWaiter, "run", side_effect=KeyboardInterrupt),
            redirect_stdout(output),
        ):
            code = wait_for.run_wait(args, ["query"])

        self.assertEqual(code, wait_for.EXIT_INTERRUPTED)
        self.assertEqual(json.loads(output.getvalue())["elapsed_seconds"], 2.5)

    def test_notification_formats_label_once(self) -> None:
        result = {
            "label": "stored label",
            "event": "ready",
            "status": "Running",
            "query_failures": 0,
            "elapsed_seconds": 1.0,
        }
        with patch.object(wait_for.subprocess, "run") as run:
            wait_for.notify_session(
                "codex",
                "thread-id",
                "unix://",
                "build 42",
                result,
                "{label}: {event} ({status})",
            )
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "build 42: ready (Running)")
        self.assertEqual(run.call_args.kwargs["timeout"], 60.0)

    def test_notification_commands_cover_supported_clients(self) -> None:
        expected = {
            "codex": ["codex", "queue", "--remote", "unix://", "--thread", "session-1", "--message", "resume"],
            "codewiz": ["codewiz", "run", "--session", "session-1", "resume"],
            "cursor": ["cursor-agent", "--print", "--resume=session-1", "resume"],
            "claude": ["claude", "--print", "--resume", "session-1", "resume"],
            "copilot": ["copilot", "--resume=session-1", "--prompt", "resume"],
        }
        for client, command in expected.items():
            with self.subTest(client=client):
                self.assertEqual(
                    wait_for.notification_command(client, "session-1", "resume", "unix://"),
                    command,
                )

        self.assertEqual(
            wait_for.notification_command(
                "cursor",
                "session-1",
                "resume",
                "unix://",
                ["--force"],
            ),
            ["cursor-agent", "--print", "--resume=session-1", "--force", "resume"],
        )

    def test_session_option_and_client_are_parsed(self) -> None:
        args = wait_for.parser().parse_args([
            "--label",
            "demo",
            "--ready",
            "Ready",
            "--client",
            "claude",
            "--session",
            "session-1",
            "--",
            "query",
        ])
        self.assertEqual(args.client, "claude")
        self.assertEqual(args.thread, "session-1")
        self.assertEqual(args.timeout, wait_for.DEFAULT_WAIT_TIMEOUT)

        for option in ("--max-consecutive-failures", "--max-notification-attempts"):
            with self.subTest(option=option), self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                wait_for.parser().parse_args([
                    "--label",
                    "demo",
                    "--ready",
                    "Ready",
                    option,
                    "0",
                    "--",
                    "query",
                ])

    def test_slash_resume_directive_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            wait_for.validate_message_template(
                wait_for.parser(),
                f"/wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                log,
            )

    def test_wait_loop_resume_template_binds_watcher_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            state = Path(directory) / "loop.json"
            template = (
                f"/wait-loop resume {state}; watcher_log={log}; "
                "event_id={event_id}; event={event}; status={status}"
            )
            wait_for.validate_message_template(
                wait_for.parser(),
                template,
                log,
                loop_state=state,
            )

            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                wait_for.validate_message_template(
                    wait_for.parser(),
                    template.replace(str(log), "/tmp/wrong.json"),
                    log,
                    loop_state=state,
                )

            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                wait_for.validate_message_template(
                    wait_for.parser(),
                    template,
                    log,
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
            self.assertTrue(wait_for.loop_wait_is_current(args))

            args.event_id = "stale"
            self.assertFalse(wait_for.loop_wait_is_current(args))

    def test_non_codex_notification_does_not_retry_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--client",
                "claude",
                "--session",
                "session-1",
                "--log-file",
                str(log),
                "--message-template",
                f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                "--",
                "query",
            ])
            result = {"event_id": "stable", "event": "ready", "status": "Ready"}
            with patch.object(
                wait_for,
                "notify_session",
                side_effect=subprocess.TimeoutExpired("claude", 60),
            ) as notify:
                code = wait_for.deliver_notification(args, result)

            self.assertEqual(code, wait_for.EXIT_NOTIFY_FAILED)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(notify.call_args.kwargs["timeout"], 3600.0)

    def test_single_notification_attempt_reports_ambiguous_failure(self) -> None:
        result = {
            "event_id": "event-1",
            "event": "ready",
            "status": "Running",
            "query_failures": 0,
            "elapsed_seconds": 1.0,
        }
        with (
            patch.object(
                wait_for.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("codex", 60),
            ) as run,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            wait_for.notify_session(
                "codex",
                "thread-id",
                "unix://",
                "build 42",
                result,
                "{event_id}",
                timeout=7,
            )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_waiter_stops_before_query_when_watch_is_cancelled(self) -> None:
        query_calls = 0

        def query(_remaining: float | None) -> str:
            nonlocal query_calls
            query_calls += 1
            return "Running"

        result, code = wait_for.StatusWaiter(
            query,
            {"Running"},
            set(),
            interval=5,
            timeout=None,
            max_consecutive_failures=3,
            is_active=lambda: False,
        ).run()

        self.assertEqual(code, wait_for.EXIT_ACTIVATION_CANCELLED)
        self.assertEqual(result["event"], "cancelled")
        self.assertEqual(query_calls, 0)

    def test_goal_activation_has_a_deadline(self) -> None:
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "goal.json"
            state.write_text(
                json.dumps({
                    "nodes": {
                        "deploy": {
                            "status": "running",
                            "wait": {
                                "phase": "prepared",
                                "watch_id": "watch-1",
                                "thread": "thread-1",
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
                thread="thread-1",
                activation_interval=0.25,
                activation_timeout=1.0,
            )
            with (
                patch.object(wait_for.time, "monotonic", side_effect=clock.now),
                patch.object(wait_for.time, "sleep", side_effect=clock.sleep),
            ):
                result = wait_for.wait_for_goal_activation(args)

        self.assertEqual(result, "timeout")
        self.assertEqual(clock.value, 1.0)

    def test_activation_interrupt_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            goal = root / "goal.json"
            log = root / "result.json"
            event_id = "watch-1"
            arguments = [
                "--label", "deployment",
                "--ready", "Ready",
                "--thread", "thread-1",
                "--lock-file", str(root / "watcher.lock"),
                "--log-file", str(log),
                "--event-id", event_id,
                "--goal-state", str(goal),
                "--goal-node", "deploy",
                "--startup-file", str(root / "started.json"),
                "--message-template",
                (
                    f"$wait-goal resume {goal}; node=deploy; watcher_log={log}; "
                    "event_id={event_id}; event={event}; status={status}"
                ),
                "--", "query",
            ]
            with (
                patch.object(wait_for, "wait_for_goal_activation", side_effect=KeyboardInterrupt),
                redirect_stdout(StringIO()),
            ):
                code = wait_for.main(arguments)

            result = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(code, wait_for.EXIT_INTERRUPTED)
            self.assertEqual(result["event"], "interrupted")
            self.assertEqual(result["event_id"], event_id)

    def test_missing_or_corrupt_goal_state_invalidates_watch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "goal.json"
            args = Namespace(goal_state=state, goal_node="deploy", event_id="watch-1")
            self.assertFalse(wait_for.goal_wait_is_current(args))

            state.write_text("{", encoding="utf-8")
            self.assertFalse(wait_for.goal_wait_is_current(args))

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
            self.assertFalse(wait_for.goal_wait_is_current(args))

            args.client = "claude"
            args.thread = "wrong-thread"
            self.assertFalse(wait_for.goal_wait_is_current(args))

    def test_cancelled_goal_suppresses_a_completed_query_notification(self) -> None:
        args = wait_for.parser().parse_args([
            "--label",
            "deployment",
            "--ready",
            "Ready",
            "--thread",
            "thread-1",
            "--lock-file",
            "/tmp/deployment.lock",
            "--log-file",
            "/tmp/deployment.json",
            "--message-template",
            "$wait-goal resume /tmp/deployment.json; event_id={event_id}; event={event}; status={status}",
            "--event-id",
            "watch-1",
            "--goal-state",
            "/tmp/goal.json",
            "--goal-node",
            "deploy",
            "--startup-file",
            "/tmp/deployment.started.json",
            "--",
            "query",
        ])
        with (
            patch.object(
                wait_for.StatusWaiter,
                "run",
                return_value=(
                    {
                        "event": "ready",
                        "status": "Ready",
                        "query_failures": 0,
                        "elapsed_seconds": 1.0,
                    },
                    wait_for.EXIT_READY,
                ),
            ),
            patch.object(wait_for, "goal_wait_is_current", return_value=False),
            patch.object(wait_for, "persist_result"),
            patch.object(wait_for, "write_result"),
            patch.object(wait_for, "deliver_notification") as deliver,
        ):
            code = wait_for.run_wait(args, ["query"])

        self.assertEqual(code, wait_for.EXIT_ACTIVATION_CANCELLED)
        deliver.assert_not_called()

    def test_delivery_retries_with_stable_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            lock = Path(directory) / "watcher.lock"
            template = f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}"
            result = {
                "event_id": "stable-event",
                "event": "ready",
                "status": "Ready",
            }
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(lock),
                "--log-file",
                str(log),
                "--message-template",
                template,
                "--notification-retry-interval",
                "0.001",
                "--max-notification-attempts",
                "3",
                "--",
                "query",
            ])
            with (
                patch.object(
                    wait_for,
                    "notify_session",
                    side_effect=[subprocess.CalledProcessError(1, "codex"), None],
                ) as notify,
                patch.object(wait_for.time, "sleep"),
            ):
                self.assertIsNone(wait_for.deliver_notification(args, result))

            persisted = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(notify.call_count, 2)
            self.assertEqual(persisted["event_id"], "stable-event")
            self.assertEqual(persisted["notification"], "queued")
            self.assertEqual(persisted["notification_attempts"], 2)

    def test_goal_cancellation_stops_notification_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "result.json"
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(root / "watcher.lock"),
                "--log-file",
                str(log),
                "--goal-state",
                str(root / "goal.json"),
                "--goal-node",
                "deploy",
                "--startup-file",
                str(root / "startup.json"),
                "--event-id",
                "watch-1",
                "--message-template",
                f"$wait-goal resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                "--",
                "query",
            ])
            result = {"event_id": "watch-1", "event": "ready", "status": "Ready"}
            with (
                patch.object(wait_for, "goal_wait_is_current", side_effect=[True, False]),
                patch.object(
                    wait_for,
                    "notify_session",
                    side_effect=subprocess.CalledProcessError(1, "codex"),
                ) as notify,
                patch.object(wait_for.time, "sleep"),
            ):
                code = wait_for.deliver_notification(args, result)

            self.assertEqual(code, wait_for.EXIT_ACTIVATION_CANCELLED)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(
                json.loads(log.read_text(encoding="utf-8"))["notification"],
                "cancelled",
            )

    def test_ambiguous_delivery_reports_failure_when_retry_limit_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(Path(directory) / "watcher.lock"),
                "--log-file",
                str(log),
                "--message-template",
                f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                "--max-notification-attempts",
                "1",
                "--",
                "query",
            ])
            result = {"event_id": "stable", "event": "ready", "status": "Ready"}
            with patch.object(
                wait_for,
                "notify_session",
                side_effect=subprocess.TimeoutExpired("codex", 60),
            ) as notify:
                code = wait_for.deliver_notification(args, result)

            self.assertEqual(code, wait_for.EXIT_NOTIFY_FAILED)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(
                json.loads(log.read_text(encoding="utf-8"))["notification"],
                "unconfirmed",
            )

    def test_ambiguous_delivery_retries_with_stable_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(Path(directory) / "watcher.lock"),
                "--log-file",
                str(log),
                "--message-template",
                f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                "--notification-retry-interval",
                "0.001",
                "--",
                "query",
            ])
            result = {"event_id": "stable", "event": "ready", "status": "Ready"}
            with (
                patch.object(
                    wait_for,
                    "notify_session",
                    side_effect=[subprocess.TimeoutExpired("codex", 60), None],
                ) as notify,
                patch.object(wait_for.time, "sleep"),
            ):
                self.assertIsNone(wait_for.deliver_notification(args, result))

            self.assertEqual(notify.call_count, 2)
            persisted = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(persisted["event_id"], "stable")
            self.assertEqual(persisted["notification"], "queued")

    def test_codex_notification_retries_are_bounded_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            args = wait_for.parser().parse_args([
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--session",
                "session-1",
                "--lock-file",
                str(Path(directory) / "watcher.lock"),
                "--log-file",
                str(log),
                "--message-template",
                f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                "--",
                "query",
            ])
            result = {"event_id": "stable", "event": "ready", "status": "Ready"}
            with (
                patch.object(
                    wait_for,
                    "notify_session",
                    side_effect=subprocess.CalledProcessError(1, "codex"),
                ) as notify,
                patch.object(wait_for.time, "sleep"),
            ):
                code = wait_for.deliver_notification(args, result)

            self.assertEqual(code, wait_for.EXIT_NOTIFY_FAILED)
            self.assertEqual(notify.call_count, 12)

    def test_cli_without_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--label",
                    "demo",
                    "--ready",
                    "Running",
                    "--log-file",
                    str(log),
                    "--",
                    sys.executable,
                    "-c",
                    "print('Running')",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"event": "ready"', log.read_text())
            self.assertIn('"event_id":', log.read_text())

    def test_notification_requires_log_and_resume_template(self) -> None:
        base_arguments = [
            "--label",
            "demo",
            "--ready",
            "Running",
            "--thread",
            "thread-1",
        ]
        command = ["--", sys.executable, "-c", "print('Running')"]

        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            wait_for.main([*base_arguments, *command])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                wait_for.main([
                    *base_arguments,
                    "--log-file",
                    str(root / "result.json"),
                    *command,
                ])

            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                wait_for.main([
                    *base_arguments,
                    "--log-file",
                    str(root / "result.json"),
                    "--lock-file",
                    str(root / "watcher.lock"),
                    "--message-template",
                    "$wait resume missing.json; event={event}",
                    *command,
                ])

    def test_rejects_overlapping_coordination_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = str(Path(directory) / "shared")
            with (
                self.assertRaises(SystemExit),
                redirect_stderr(StringIO()),
                patch.object(wait_for.subprocess, "run") as run,
            ):
                wait_for.main([
                    "--label",
                    "demo",
                    "--ready",
                    "Ready",
                    "--thread",
                    "thread-1",
                    "--lock-file",
                    shared,
                    "--log-file",
                    shared,
                    "--message-template",
                    f"$wait resume {shared}; event_id={{event_id}}; event={{event}}; status={{status}}",
                    "--",
                    "query",
                ])
            run.assert_not_called()

    def test_rejects_overlapping_ready_and_terminal_statuses(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            wait_for.main([
                "--label",
                "demo",
                "--ready",
                "Failed",
                "--terminal",
                "Failed",
                "--",
                "query",
            ])

    def test_rejects_invalid_resume_template_before_querying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "result.json"
            arguments = [
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(root / "watcher.lock"),
                "--log-file",
                str(log),
                "--message-template",
                f"$wait resume {log}; event_id={{event_id:bad}}; event={{event}}; status={{status}}",
                "--",
                "query",
            ]
            with (
                patch.object(wait_for.subprocess, "run") as run,
                self.assertRaises(SystemExit),
                redirect_stderr(StringIO()),
            ):
                wait_for.main(arguments)
            run.assert_not_called()

    def test_goal_resume_template_must_match_exact_state_and_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            goal = root / "goal.json"
            log = root / "result.json"
            base_arguments = [
                "--label",
                "demo",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--event-id",
                "watch-1",
                "--goal-state",
                str(goal),
                "--goal-node",
                "deploy",
                "--startup-file",
                str(root / "started.json"),
                "--lock-file",
                str(root / "watcher.lock"),
                "--log-file",
                str(log),
            ]
            templates = [
                (
                    f"$wait-goal resume {goal}-old; node=deploy; watcher_log={log}; "
                    "event_id={event_id}; event={event}; status={status}"
                ),
                (
                    f"$wait-goal resume {goal}; node=deploy-old; watcher_log={log}; "
                    "event_id={event_id}; event={event}; status={status}"
                ),
                (
                    f"$wait-goal resume {goal}; node=deploy; watcher_log={log}.wrong; "
                    f"note={log}; event_id={{event_id}}; event={{event}}; status={{status}}"
                ),
                (
                    f"$wait-goal resume {goal}-old; node=old; watcher_log={log}.old; "
                    f"$wait-goal resume {goal}; node=deploy; watcher_log={log}; "
                    "event_id={event_id}; event={event}; status={status}"
                ),
            ]
            for template in templates:
                with (
                    self.subTest(template=template),
                    patch.object(wait_for.subprocess, "run") as run,
                    self.assertRaises(SystemExit),
                    redirect_stderr(StringIO()),
                ):
                    wait_for.main([
                        *base_arguments,
                        "--message-template",
                        template,
                        "--",
                        "query",
                    ])
                run.assert_not_called()

    def test_standalone_resume_template_rejects_duplicate_directives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "result.json"
            wrong_log = root / "wrong.json"
            template = (
                f"$wait resume {wrong_log}; $wait resume {log}; "
                "event_id={event_id}; event={event}; status={status}"
            )
            with (
                patch.object(wait_for.subprocess, "run") as run,
                self.assertRaises(SystemExit),
                redirect_stderr(StringIO()),
            ):
                wait_for.main([
                    "--label",
                    "demo",
                    "--ready",
                    "Ready",
                    "--thread",
                    "thread-1",
                    "--lock-file",
                    str(root / "watcher.lock"),
                    "--log-file",
                    str(log),
                    "--message-template",
                    template,
                    "--",
                    "query",
                ])
            run.assert_not_called()

    def test_lock_is_released_after_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "watcher.lock"
            with redirect_stdout(StringIO()):
                code = wait_for.main([
                    "--label",
                    "demo",
                    "--ready",
                    "Running",
                    "--lock-file",
                    str(lock_path),
                    "--",
                    sys.executable,
                    "-c",
                    "print('Running')",
                ])
            self.assertEqual(code, wait_for.EXIT_READY)

            with lock_path.open("a", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_duplicate_watcher_does_not_overwrite_owner_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "watcher.lock"
            log_path = root / "watcher.json"
            owner_result = '{"event": "ready"}\n'
            log_path.write_text(owner_result, encoding="utf-8")

            with lock_path.open("a", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with redirect_stdout(StringIO()):
                    code = wait_for.main([
                        "--label",
                        "demo",
                        "--ready",
                        "Running",
                        "--lock-file",
                        str(lock_path),
                        "--log-file",
                        str(log_path),
                        "--",
                        sys.executable,
                        "-c",
                        "print('Running')",
                    ])

            self.assertEqual(code, wait_for.EXIT_ALREADY_WATCHING)
            self.assertEqual(log_path.read_text(encoding="utf-8"), owner_result)

    def test_persists_event_before_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"

            def notify(*_args: object, **_kwargs: object) -> None:
                persisted = json.loads(log.read_text())
                self.assertEqual(persisted["event"], "ready")
                self.assertTrue(persisted["event_id"])

            with (
                patch.object(wait_for, "notify_session", side_effect=notify),
                redirect_stdout(StringIO()),
            ):
                code = wait_for.main([
                    "--label",
                    "demo",
                    "--ready",
                    "Running",
                    "--thread",
                    "thread-1",
                    "--lock-file",
                    str(Path(directory) / "watcher.lock"),
                    "--log-file",
                    str(log),
                    "--message-template",
                    f"$wait resume {log}; event_id={{event_id}}; event={{event}}; status={{status}}",
                    "--",
                    sys.executable,
                    "-c",
                    "print('Running')",
                ])
            self.assertEqual(code, wait_for.EXIT_READY)

    def test_cli_overall_timeout_limits_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "result.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--label",
                    "demo",
                    "--ready",
                    "Running",
                    "--timeout",
                    "0.05",
                    "--query-timeout",
                    "1",
                    "--log-file",
                    str(log),
                    "--",
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.3); print('Running')",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, wait_for.EXIT_TIMEOUT, result.stderr)
            self.assertIn('"event": "timeout"', log.read_text())


if __name__ == "__main__":
    unittest.main()
