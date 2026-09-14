from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).parents[1]


def load_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wait_for = load_script("wait_for_integration", ROOT / "scripts" / "wait_for.py")
wait_goal = load_script("wait_goal_integration", ROOT / "scripts" / "wait_goal.py")


class WaitIntegrationTest(unittest.TestCase):
    def test_watcher_event_resumes_external_goal_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "goal.json"
            log_path = root / "watcher.json"
            lock_path = root / "watcher.lock"
            startup_path = root / "watcher.started.json"

            def goal_cli(*arguments: str) -> dict[str, object]:
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(wait_goal.main(list(arguments)), 0)
                return json.loads(output.getvalue())

            goal_cli(
                "init",
                "--state",
                str(state_path),
                "--objective",
                "deploy safely",
                "--thread",
                "thread-1",
            )
            goal_cli(
                "add",
                "--state",
                str(state_path),
                "--id",
                "deploy",
                "--title",
                "Deploy",
                "--kind",
                "external",
            )
            goal_cli("start", "--state", str(state_path), "--id", "deploy")
            waiting = goal_cli(
                "wait",
                "--state",
                str(state_path),
                "--id",
                "deploy",
                "--label",
                "deployment",
                "--log-file",
                str(log_path),
                "--lock-file",
                str(lock_path),
                "--startup-file",
                str(startup_path),
            )
            watch_id = str(waiting["watch_id"])
            self.assertEqual(waiting["status"], "running")
            self.assertEqual(waiting["wait"], "prepared")
            template = (
                f"$wait-goal resume {state_path}; node=deploy; watcher_log={log_path}; "
                "event_id={event_id}; event={event}; status={status}"
            )
            queued_messages: list[str] = []
            query_calls = 0

            def query(_command: list[str], _timeout: float, _json_path: str | None) -> str:
                nonlocal query_calls
                query_calls += 1
                return "Ready"

            def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[:2], ["codex", "queue"])
                message = command[-1]
                queued_messages.append(message)
                event = json.loads(log_path.read_text(encoding="utf-8"))
                goal_cli(
                    "wake",
                    "--state",
                    str(state_path),
                    "--id",
                    "deploy",
                    "--event-id",
                    str(event["event_id"]),
                    "--event",
                    str(event["event"]),
                    "--external-status",
                    str(event["status"]),
                )
                return subprocess.CompletedProcess(command, 0, "queued\n", "")

            watcher_arguments = [
                "--label",
                "deployment",
                "--ready",
                "Ready",
                "--thread",
                "thread-1",
                "--lock-file",
                str(lock_path),
                "--log-file",
                str(log_path),
                "--event-id",
                watch_id,
                "--goal-state",
                str(state_path),
                "--goal-node",
                "deploy",
                "--startup-file",
                str(startup_path),
                "--activation-interval",
                "0.001",
                "--message-template",
                template,
                "--",
                "query-tool",
            ]

            def run_watcher() -> int:
                with redirect_stdout(StringIO()):
                    return wait_for.main(watcher_arguments)

            with (
                patch.object(wait_for, "query_status", side_effect=query),
                patch.object(wait_for.subprocess, "run", side_effect=run),
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_watcher)
                    deadline = time.monotonic() + 2
                    while not startup_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.001)
                    self.assertTrue(startup_path.exists())
                    self.assertEqual(
                        wait_goal.GoalStore(state_path).load().get_node("deploy")["status"],
                        "running",
                    )
                    self.assertEqual(queued_messages, [])
                    self.assertEqual(query_calls, 0)
                    goal_cli(
                        "activate-wait",
                        "--state",
                        str(state_path),
                        "--id",
                        "deploy",
                        "--watch-id",
                        watch_id,
                    )
                    code = future.result(timeout=2)
            self.assertEqual(code, wait_for.EXIT_READY)

            event = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(event["event_id"], watch_id)
            self.assertEqual(event["notification"], "queued")
            self.assertEqual(len(queued_messages), 1)
            self.assertEqual(query_calls, 1)
            self.assertIn(f"event_id={watch_id}", queued_messages[0])
            self.assertEqual(
                wait_goal.GoalStore(state_path).load().get_node("deploy")["status"],
                "running",
            )


if __name__ == "__main__":
    unittest.main()
