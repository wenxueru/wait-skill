from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from wait_for import QueryFailed, poll, run_command  # noqa: E402


class PollTest(unittest.TestCase):
    def test_removed_cli_fails_explicitly(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).parents[1] / "src" / "wait_for.py"), "--ready", "Ready"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Import poll", completed.stderr)

    def test_custom_evaluation_keeps_history(self) -> None:
        values = iter([{"healthy": 1}, {"healthy": 2}, {"healthy": 3}])
        seen = []

        def evaluate(data):
            seen.append(data["healthy"])
            return {"workers": seen.copy()} if sum(seen) >= 6 else None

        self.assertEqual(poll(lambda: next(values), evaluate, interval=.001), {"workers": [1, 2, 3]})

    def test_falsey_results_finish(self) -> None:
        for result in (False, 0, "", [], {}):
            with self.subTest(result=result):
                self.assertIs(poll(lambda: "anything", lambda data: result), result)

    def test_successful_query_resets_failure_count(self) -> None:
        query = Mock(side_effect=[OSError("offline"), 1, OSError("offline"), 2])
        result = poll(query, lambda data: data if data == 2 else None,
                      interval=.001, max_consecutive_failures=2)
        self.assertEqual(result, 2)
        self.assertEqual(query.call_count, 4)

    def test_repeated_query_errors_stop(self) -> None:
        query = Mock(side_effect=OSError("offline"))
        with self.assertRaises(QueryFailed):
            poll(query, lambda data: data, interval=.001, max_consecutive_failures=2)
        self.assertEqual(query.call_count, 2)

    def test_configuration_and_evaluation_errors_do_not_retry(self) -> None:
        for query, evaluate in (
            (Mock(side_effect=ValueError("bad JSON")), Mock()),
            (Mock(return_value=1), Mock(side_effect=OSError("bad evaluator"))),
        ):
            with self.subTest(query=query), self.assertRaises((ValueError, OSError)):
                poll(query, evaluate)
            self.assertEqual(query.call_count, 1)

    def test_stuck_query_is_interrupted(self) -> None:
        start = time.monotonic()
        with self.assertRaises(QueryFailed):
            poll(lambda: time.sleep(30), lambda data: data,
                 query_timeout=.02, max_consecutive_failures=1)
        self.assertLess(time.monotonic() - start, 1)

    def test_total_timeout_interrupts_query_and_evaluator(self) -> None:
        for query, evaluate in (
            (lambda: time.sleep(30), lambda data: data),
            (lambda: 1, lambda data: time.sleep(30)),
        ):
            start = time.monotonic()
            with self.subTest(query=query), self.assertRaises(TimeoutError):
                poll(query, evaluate, timeout=.02)
            self.assertLess(time.monotonic() - start, 1)

    def test_sleep_respects_total_deadline(self) -> None:
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            poll(lambda: 1, lambda data: None, interval=30, timeout=.02)
        self.assertLess(time.monotonic() - start, 1)

    def test_restores_signal_handler_after_success_and_error(self) -> None:
        handler = signal.getsignal(signal.SIGALRM)
        for query in (lambda: 1, Mock(side_effect=ValueError("bad"))):
            try:
                poll(query, lambda data: data)
            except ValueError:
                pass
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    def test_existing_timer_is_not_overwritten(self) -> None:
        signal.setitimer(signal.ITIMER_REAL, 30)
        try:
            with self.assertRaises(RuntimeError):
                poll(lambda: 1, lambda data: data)
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 25)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_worker_thread_is_rejected(self) -> None:
        with ThreadPoolExecutor() as executor, self.assertRaises(RuntimeError):
            executor.submit(poll, lambda: 1, lambda data: data).result()

    def test_invalid_limits(self) -> None:
        for name in ("interval", "timeout", "query_timeout"):
            for value in (0, -1, float("nan"), float("inf")):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    poll(lambda: 1, lambda data: data, **{name: value})
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                poll(lambda: 1, lambda data: data, max_consecutive_failures=value)

    def test_command_preserves_output_and_reports_failure(self) -> None:
        self.assertEqual(run_command([sys.executable, "-c", "print('  任意 JSON/text  ')"]), "  任意 JSON/text  \n")
        with self.assertRaises(subprocess.CalledProcessError):
            run_command([sys.executable, "-c", "raise SystemExit(7)"])

    def test_interrupted_command_is_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            command = [sys.executable, "-c",
                       f"import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)"]
            with self.assertRaises(QueryFailed):
                poll(lambda: run_command(command), lambda data: data,
                     query_timeout=.2, max_consecutive_failures=1)
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
