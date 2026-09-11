from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
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

    def test_ready_after_transient_failure(self) -> None:
        clock = Clock()
        outcomes = iter([RuntimeError("secret raw response"), "Queued", "Running"])

        def query(_remaining: float | None) -> str:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result, code = wait_for.wait_for_status(
            query,
            {"Running"},
            {"Failed"},
            interval=5,
            timeout=None,
            max_consecutive_failures=3,
            now=clock.now,
            sleep=clock.sleep,
        )
        self.assertEqual(code, wait_for.EXIT_READY)
        self.assertEqual(result["event"], "ready")
        self.assertEqual(result["query_failures"], 1)
        self.assertNotIn("secret", str(result))

    def test_repeated_query_failures_stop(self) -> None:
        clock = Clock()

        def query(_remaining: float | None) -> str:
            raise RuntimeError("unavailable")

        result, code = wait_for.wait_for_status(
            query,
            {"Running"},
            set(),
            interval=5,
            timeout=None,
            max_consecutive_failures=2,
            now=clock.now,
            sleep=clock.sleep,
        )
        self.assertEqual(code, wait_for.EXIT_QUERY_FAILED)
        self.assertEqual(result["query_failures"], 2)

    def test_result_returned_after_deadline_is_rejected(self) -> None:
        clock = Clock()
        budgets = []

        def query(remaining: float | None) -> str:
            budgets.append(remaining)
            clock.sleep(20)
            return "Running"

        result, code = wait_for.wait_for_status(
            query,
            {"Running"},
            set(),
            interval=5,
            timeout=1,
            max_consecutive_failures=3,
            now=clock.now,
            sleep=clock.sleep,
        )
        self.assertEqual(code, wait_for.EXIT_TIMEOUT)
        self.assertEqual(result["event"], "timeout")
        self.assertIsNone(result["status"])
        self.assertEqual(budgets, [1])

    def test_notification_formats_label_once(self) -> None:
        result = {
            "label": "stored label",
            "event": "ready",
            "status": "Running",
            "query_failures": 0,
            "elapsed_seconds": 1.0,
        }
        with patch.object(wait_for.subprocess, "run") as run:
            wait_for.notify_thread(
                "thread-id",
                "unix://",
                "build 42",
                result,
                "{label}: {event} ({status})",
            )
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "build 42: ready (Running)")

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
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"event": "ready"', log.read_text())

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
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, wait_for.EXIT_TIMEOUT, result.stderr)
            self.assertIn('"event": "timeout"', log.read_text())


if __name__ == "__main__":
    unittest.main()
