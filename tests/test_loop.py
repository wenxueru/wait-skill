from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
SCRIPT = Path(__file__).parents[1] / "src" / "loop.py"
SPEC = importlib.util.spec_from_file_location("loop", SCRIPT)
assert SPEC and SPEC.loader
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


class WaitLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / "loop.json"

    def invoke(
        self,
        handler: Callable[[Namespace], None],
        **arguments: object,
    ) -> dict[str, object]:
        output = StringIO()
        with redirect_stdout(output):
            handler(Namespace(**arguments))
        return json.loads(output.getvalue())

    def init(self, *, max_iterations: int | None = None) -> dict[str, object]:
        with patch.object(loop.time, "time", return_value=100.0):
            return self.invoke(
                loop.command_init,
                state=self.state,
                task="check the queue",
                event_note="Begin the next queue check",
                client="codex",
                session="session-1",
                interval=60.0,
                duration=3600.0,
                max_iterations=max_iterations,
            )

    def test_iteration_lifecycle_and_duplicate_event(self) -> None:
        self.init(max_iterations=2)
        with patch.object(loop.time, "time", return_value=110.0):
            scheduled = self.invoke(loop.command_complete, state=self.state, summary="first")
        watch_id = str(scheduled["watch_id"])
        self.assertEqual(scheduled["phase"], "waiting")
        self.assertEqual(scheduled["next_run_at"], 170.0)

        with patch.object(loop.time, "time", return_value=169.0):
            output = StringIO()
            with redirect_stdout(output):
                loop.command_due(Namespace(state=self.state, watch_id=watch_id))
        self.assertEqual(output.getvalue().strip(), "Waiting")

        with patch.object(loop.time, "time", return_value=170.0):
            resumed = self.invoke(
                loop.command_begin,
                state=self.state,
                event_id=watch_id,
            )
        self.assertEqual(resumed["iteration"], 2)
        self.assertEqual(resumed["phase"], "running")

        duplicate = self.invoke(
            loop.command_begin,
            state=self.state,
            event_id=watch_id,
        )
        self.assertEqual(duplicate, {"duplicate": True, "iteration": 2})

        with patch.object(loop.time, "time", return_value=180.0):
            completed = self.invoke(loop.command_complete, state=self.state, summary="second")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(completed["phase"])
        self.assertEqual(len(completed["runs"]), 2)

    def test_duration_expiry_is_bounded_and_idempotent(self) -> None:
        self.init()
        with patch.object(loop.time, "time", return_value=100.0):
            scheduled = self.invoke(loop.command_complete, state=self.state, summary="first")
        watch_id = str(scheduled["watch_id"])

        with patch.object(loop.time, "time", return_value=3700.0):
            output = StringIO()
            with redirect_stdout(output):
                loop.command_due(Namespace(state=self.state, watch_id=watch_id))
            expired = self.invoke(loop.command_expire, state=self.state, event_id=watch_id)
        self.assertEqual(output.getvalue().strip(), "Expired")
        self.assertEqual(expired["status"], "completed")

        duplicate = self.invoke(loop.command_expire, state=self.state, event_id=watch_id)
        self.assertEqual(duplicate, {"duplicate": True, "status": "completed"})

    def test_ready_event_delivered_after_deadline_completes_loop(self) -> None:
        self.init()
        with patch.object(loop.time, "time", return_value=100.0):
            scheduled = self.invoke(loop.command_complete, state=self.state, summary="first")

        with patch.object(loop.time, "time", return_value=3700.0):
            completed = self.invoke(
                loop.command_begin,
                state=self.state,
                event_id=scheduled["watch_id"],
            )

        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(completed["phase"])
        self.assertIsNone(completed["watch_id"])
        self.assertEqual(completed["last_event_id"], scheduled["watch_id"])
        self.assertEqual(completed["events"][-1]["operation"], "expire")

    def test_remote_is_persisted_and_propagated_to_timers(self) -> None:
        with patch.object(loop.time, "time", return_value=100.0):
            self.invoke(
                loop.command_init,
                state=self.state,
                task="check the queue",
                event_note="Begin the next queue check",
                client="codex",
                session="session-1",
                remote="ws://127.0.0.1:8000",
                interval=60.0,
                duration=3600.0,
                max_iterations=None,
            )
            scheduled = self.invoke(loop.command_complete, state=self.state, summary="first")
        self.assertEqual(json.loads(self.state.read_text())["remote"], "ws://127.0.0.1:8000")
        argv = loop.timer_argv(loop.LoopState(scheduled), self.state)
        self.assertEqual(argv[argv.index("--remote") + 1], "ws://127.0.0.1:8000")

    def test_remote_must_be_a_string_or_null(self) -> None:
        self.init()
        state = loop.LoopStore(self.state).load().data
        state["remote"] = 5
        with self.assertRaises(loop.LoopError):
            loop.LoopState(state)

    def test_default_state_is_unique_and_project_scoped(self) -> None:
        project = Path(self.directory.name) / "demo"
        nested = project / "src"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()

        first = loop.default_state_path(nested)
        second = loop.default_state_path(project)
        self.assertEqual(first.parent, second.parent)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parents[1], loop.DEFAULT_STATE_ROOT)

    def test_init_without_state_returns_canonical_default_path(self) -> None:
        state = Path(self.directory.name) / "default.json"
        args = loop.parser().parse_args([
            "init",
            "--task",
            "check the queue",
            "--event-note",
            "Begin the next queue check",
            "--interval",
            "60",
            "--session",
            "session-1",
        ])
        output = StringIO()
        with (
            patch.object(loop, "default_state_path", return_value=state),
            redirect_stdout(output),
        ):
            args.handler(args)

        self.assertEqual(json.loads(output.getvalue())["state_file"], os.path.realpath(state))
        self.assertTrue(state.exists())


if __name__ == "__main__":
    unittest.main()
