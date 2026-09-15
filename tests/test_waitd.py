from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import waitd  # noqa: E402
import waitctl  # noqa: E402
import state_runner  # noqa: E402


class WaitDaemonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.daemon = waitd.WaitDaemon(self.root / "registry.json")

    def watcher_argv(self, status: str, *, interval: float = 0.01) -> list[str]:
        return [
            "--label",
            "demo",
            "--ready",
            "Ready",
            "--interval",
            str(interval),
            "--timeout",
            "2",
            "--query-timeout",
            "1",
            "--lock-file",
            str(self.root / "watch.lock"),
            "--log-file",
            str(self.root / "watch.json"),
            "--",
            sys.executable,
            "-c",
            f"print({status!r})",
        ]

    async def test_cancelled_query_is_reaped_before_lock_release(self) -> None:
        pid_file = self.root / "query.pid"
        argv = self.watcher_argv("Waiting")
        argv[-1] = f"import os,time; from pathlib import Path; Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
        submitted = self.daemon.submit(argv, str(self.root))
        task = self.daemon.tasks[submitted["watch_id"]]
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(.01)
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text())
        task.cancel()
        await task
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with (self.root / "watch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    async def test_async_query_limits_output_and_time(self) -> None:
        with self.assertRaises(ValueError):
            await waitd.query_status([sys.executable, "-c", "print('x'*100000)"], 2, None, str(self.root))
        with self.assertRaises(asyncio.TimeoutError):
            await waitd.query_status([sys.executable, "-c", "import time; time.sleep(30)"], .05, None, str(self.root))

    async def test_activation_timeout_writes_log(self) -> None:
        args, command = waitd.wait_for.parse_wait_args(self.watcher_argv("Ready"))
        args.goal_state = self.root / "goal.json"
        args.goal_node = "deploy"
        args.startup_file = self.root / "started.json"
        args.event_id = "activation"
        record = {"watch_id": "activation", "phase": "activating", "state": "active",
                  "deadline_at": time.time()+1, "submitted_at": time.time(),
                  "activation_deadline_at": time.time()-1}
        with patch.object(waitd.wait_for, "goal_wait_phase", return_value="prepared"):
            result, code = await self.daemon._watch(args, command, record, asyncio.Event())
        self.assertEqual(code, 76)
        self.assertEqual(result["event"], "activation_timeout")
        self.assertEqual(json.loads(args.log_file.read_text()), result)

    async def test_delivery_launch_failure_is_persisted(self) -> None:
        args, _ = waitd.wait_for.parse_wait_args(self.watcher_argv("Ready"))
        args.thread = "session"
        args.message_template = "{event_id} {event} {status}"
        result = {"event_id": "delivery", "event": "ready", "status": "Ready"}
        record = {"watch_id": "delivery", "cwd": str(self.root), "state": "active"}
        with patch.object(asyncio, "create_subprocess_exec", side_effect=FileNotFoundError):
            code = await self.daemon._deliver(args, result, record, asyncio.Event())
        self.assertEqual(code, 70)
        self.assertEqual(json.loads(args.log_file.read_text())["notification"], "failed")

    def test_relative_template_paths_are_normalized_for_all_modes(self) -> None:
        for mode in ("wait", "wait-loop", "wait-goal"):
            with self.subTest(mode=mode):
                target = "log.json" if mode == "wait" else "state.json"
                argv = ["--label", "x", "--ready", "Ready", "--log-file", "log.json",
                        "--lock-file", "lock", "--session", "session", "--event-id", "event"]
                template = f"${mode} resume {target}; event_id={{event_id}}; event={{event}}; status={{status}}"
                if mode != "wait":
                    argv += ["--loop-state" if mode == "wait-loop" else "--goal-state", target]
                    template += "; watcher_log=log.json"
                if mode == "wait-goal":
                    argv += ["--goal-node", "n", "--startup-file", "started.json"]
                    template += "; node=n"
                argv += ["--message-template", template, "--", "true"]
                args, _ = waitd.wait_for.parse_wait_args(waitd.absolute_wait_argv(argv, self.root))
                self.assertIn(str(self.root / target), args.message_template)

    async def test_loop_restart_repairs_missing_timer_registration(self) -> None:
        path = self.root / "loop.json"
        await self.daemon.dispatch({"operation": "loop", "cwd": str(self.root), "argv": [
            "init", "--state", str(path), "--task", "inspect", "--interval", "60", "--session", "s"]})
        # Simulate a committed complete followed by a crash before registration.
        await self.daemon.state_commands.run("loop", ["complete", "--state", str(path), "--summary", "done"], self.root)
        state = json.loads(path.read_text())
        restored = waitd.WaitDaemon(self.root / "registry.json")
        await restored.restore()
        watch_id = state["watch_id"]
        self.assertIn(watch_id, restored.watchers)
        await restored.reconcile_loops()
        self.assertEqual(len(restored.tasks), 1)
        task = restored.tasks[watch_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_goal_monitor_is_linked_and_cancelled_with_goal(self) -> None:
        goal = self.root / "goal.json"
        goal.write_text(json.dumps({"status": "open", "thread": "s", "nodes": {"n": {"status": "running"}}}))
        path = self.root / "monitor.json"
        response = await self.daemon.dispatch({"operation": "loop", "cwd": str(self.root), "argv": [
            "init", "--state", str(path), "--task", "inspect", "--interval", "60", "--session", "s",
            "--goal-state", str(goal), "--goal-node", "n"]})
        self.assertEqual(response["code"], 0)
        self.assertEqual((await self.daemon.reconcile_loops())[0]["goal"]["node"], "n")
        goal.write_text(json.dumps({"status": "cancelled", "nodes": {"n": {"status": "cancelled"}}}))
        await self.daemon.reconcile_loops()
        self.assertEqual(json.loads(path.read_text())["status"], "cancelled")

    async def test_complete_registers_timer_and_delivers_without_manual_start(self) -> None:
        path = self.root / "loop.json"
        await self.daemon.dispatch({"operation": "loop", "cwd": str(self.root), "argv": [
            "init", "--state", str(path), "--task", "inspect", "--interval", ".01", "--session", "s"]})
        with patch.object(self.daemon, "_deliver", new_callable=AsyncMock, return_value=None) as deliver:
            response = await self.daemon.dispatch({"operation": "loop", "cwd": str(self.root), "argv": [
                "complete", "--state", str(path), "--summary", "done"]})
            watch_id = response["output"]["watch_id"]
            await asyncio.wait_for(self.daemon.tasks[watch_id], 2)
            self.assertEqual(self.daemon.watchers[watch_id]["result"]["event"], "ready")
            deliver.assert_awaited_once()

    async def test_submit_runs_watcher_and_persists_result(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Ready"), str(self.root))
        watch_id = str(submitted["watch_id"])
        await asyncio.wait_for(self.daemon.tasks[watch_id], timeout=2)

        record = self.daemon.watchers[watch_id]
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["result"]["event"], "ready")
        self.assertEqual(json.loads((self.root / "watch.json").read_text())["event_id"], watch_id)
        await asyncio.sleep(0)
        self.assertNotIn(watch_id, self.daemon.tasks)

    async def test_cancel_interrupts_interval_and_releases_lock(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Waiting", interval=60), str(self.root))
        watch_id = str(submitted["watch_id"])
        await asyncio.sleep(0.1)

        cancelled = self.daemon.cancel(watch_id)
        await asyncio.wait_for(self.daemon.tasks[watch_id], timeout=2)

        self.assertEqual(cancelled["state"], "cancelled")
        with (self.root / "watch.lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    async def test_event_id_cannot_overwrite_a_finished_watch(self) -> None:
        argv = ["--event-id", "stable-id", *self.watcher_argv("Ready")]
        submitted = self.daemon.submit(argv, str(self.root))
        await asyncio.wait_for(self.daemon.tasks[str(submitted["watch_id"])], timeout=2)

        with self.assertRaisesRegex(ValueError, "already exists"):
            self.daemon.submit(argv, str(self.root))

    async def test_cancel_interrupts_goal_activation_sleep(self) -> None:
        state = self.root / "goal.json"
        state.write_text(
            json.dumps(
                {
                    "nodes": {
                        "deploy": {
                            "status": "running",
                            "wait": {
                                "watch_id": "watch-1",
                                "client": "codex",
                                "thread": "session-1",
                                "phase": "prepared",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            label="deployment",
            goal_state=state,
            goal_node="deploy",
            event_id="watch-1",
            client="codex",
            thread="session-1",
            log_file=self.root / "watch.json",
            lock_file=self.root / "watch.lock",
            startup_file=self.root / "watch.started.json",
            activation_timeout=60,
            activation_interval=60,
        )
        cancelled = asyncio.Event()
        activation = asyncio.create_task(  # noqa: SLF001
            self.daemon._activate_goal_wait(args, cancelled, time.time() + 60)
        )
        await asyncio.sleep(0.01)
        cancelled.set()

        self.assertEqual(await asyncio.wait_for(activation, timeout=1), "cancelled")

    async def test_activation_uses_persisted_absolute_deadline(self) -> None:
        args = argparse.Namespace(
            label="deployment",
            goal_state=self.root / "goal.json",
            goal_node="deploy",
            event_id="watch-1",
            client="codex",
            thread="session-1",
            log_file=self.root / "watch.json",
            lock_file=self.root / "watch.lock",
            startup_file=self.root / "watch.started.json",
            activation_interval=60,
        )
        args.goal_state.write_text(
            json.dumps(
                {
                    "nodes": {
                        "deploy": {
                            "status": "running",
                            "wait": {
                                "watch_id": "watch-1",
                                "client": "codex",
                                "thread": "session-1",
                                "phase": "prepared",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        outcome = await asyncio.wait_for(
            self.daemon._activate_goal_wait(args, asyncio.Event(), time.time() + 0.01),  # noqa: SLF001
            timeout=1,
        )

        self.assertEqual(outcome, "timeout")

    async def test_uncertain_delivery_is_not_replayed(self) -> None:
        log_file = self.root / "watch.json"
        args = argparse.Namespace(
            lock_file=self.root / "watch.lock",
            goal_state=None,
            log_file=log_file,
        )
        result = {
            "event": "ready",
            "event_id": "watch-1",
            "notification": "attempting",
            "notification_attempts": 1,
        }
        record = {
            "watch_id": "watch-1",
            "cwd": str(self.root),
            "state": "active",
            "phase": "notifying",
            "code": 0,
            "result": result,
        }

        resumed, code = await self.daemon._watch(  # noqa: SLF001
            args,
            [sys.executable, "-c", "raise SystemExit('must not run')"],
            record,
            asyncio.Event(),
        )

        self.assertEqual(code, waitd.wait_for.EXIT_NOTIFY_FAILED)
        self.assertEqual(resumed["notification"], "unconfirmed")
        self.assertEqual(record["phase"], "finalizing")

    async def test_awaiting_ack_resumes_without_query_or_delivery(self) -> None:
        state = self.root / "goal.json"
        state.write_text(
            json.dumps(
                {
                    "nodes": {
                        "deploy": {
                            "status": "waiting",
                            "wait": {
                                "watch_id": "watch-1",
                                "client": "codex",
                                "thread": "session-1",
                                "phase": "active",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            lock_file=self.root / "watch.lock",
            goal_state=state,
            goal_node="deploy",
            event_id="watch-1",
            client="codex",
            thread="session-1",
            log_file=self.root / "watch.json",
            activation_interval=60,
        )
        result = {
            "event": "ready",
            "event_id": "watch-1",
            "notification": "queued",
            "notification_attempts": 1,
            "delivered_at": time.time(),
        }
        record = {
            "watch_id": "watch-1",
            "cwd": str(self.root),
            "state": "active",
            "phase": "awaiting_ack",
            "wake_ack_deadline_at": time.time() + 0.01,
            "code": 0,
            "result": result,
        }

        resumed, code = await self.daemon._watch(  # noqa: SLF001
            args,
            [sys.executable, "-c", "raise SystemExit('must not run')"],
            record,
            asyncio.Event(),
        )

        self.assertEqual(code, 0)
        self.assertEqual(resumed, result)
        self.assertEqual(record["phase"], "finalizing")

    def test_registry_retains_only_recent_finished_watches(self) -> None:
        for index in range(waitd.MAX_FINISHED_WATCHES + 2):
            self.daemon.watchers[str(index)] = {"state": "completed", "updated_at": index}
        self.daemon.watchers["active"] = {"state": "active", "updated_at": 0}

        self.daemon.save()

        self.assertIn("active", self.daemon.watchers)
        self.assertEqual(len(self.daemon.watchers), waitd.MAX_FINISHED_WATCHES + 1)

    def test_invalid_registry_prevents_startup(self) -> None:
        registry = self.root / "invalid-registry.json"
        registry.write_text("not json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            waitd.WaitDaemon(registry)

    async def test_state_command_timeout_includes_lock_wait(self) -> None:
        runner = state_runner.StateCommandRunner()
        await runner.locks["goal"].acquire()
        original_timeout = state_runner.STATE_COMMAND_TIMEOUT
        state_runner.STATE_COMMAND_TIMEOUT = 0.01
        try:
            result = await asyncio.wait_for(
                runner.run("goal", ["check"], self.root),
                timeout=0.1,
            )
        finally:
            state_runner.STATE_COMMAND_TIMEOUT = original_timeout
            runner.locks["goal"].release()

        self.assertEqual(result["code"], 124)

    async def test_state_commands_run_through_service(self) -> None:
        goal_state = self.root / "goal.json"
        response = await self.daemon.dispatch(
            {
                "operation": "goal",
                "argv": [
                    "init",
                    "--state",
                    str(goal_state),
                    "--objective",
                    "ship safely",
                    "--session",
                    "session-1",
                ],
                "cwd": str(self.root),
            },
        )

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["output"]["objective"], "ship safely")
        self.assertTrue(goal_state.exists())

        loop_state = self.root / "loop.json"
        response = await self.daemon.dispatch(
            {
                "operation": "loop",
                "argv": [
                    "init",
                    "--state",
                    str(loop_state),
                    "--task",
                    "inspect queue",
                    "--interval",
                    "60",
                    "--session",
                    "session-1",
                ],
                "cwd": str(self.root),
            },
        )

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["output"]["task"], "inspect queue")
        self.assertTrue(loop_state.exists())

    def test_query_arguments_are_not_rewritten_as_coordination_paths(self) -> None:
        argv = ["--log-file", "watch.json", "--", "query", "--log-file", "remote.json"]
        self.assertEqual(
            waitd.absolute_wait_argv(argv, self.root),
            ["--log-file", str(self.root / "watch.json"), "--", "query", "--log-file", "remote.json"],
        )


class WaitServiceTest(unittest.TestCase):
    def test_unix_socket_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            socket_path = root / "waitd.sock"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPTS / "waitd.py"),
                    "serve",
                    "--socket",
                    str(socket_path),
                    "--registry",
                    str(root / "registry.json"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 2
                while True:
                    try:
                        response = waitctl.request({"operation": "ping"}, socket_path)
                        break
                    except OSError:
                        if process.poll() is not None:
                            error = process.communicate()[1]
                            if "Operation not permitted" in error:
                                self.skipTest("sandbox does not permit Unix sockets")
                            self.fail(f"waitd exited during startup: {error.strip()}")
                        if time.monotonic() >= deadline:
                            self.fail("waitd did not create its control socket")
                        time.sleep(0.01)
                self.assertEqual(response["status"], "ok")
                self.assertEqual(response["protocol_version"], waitd.PROTOCOL_VERSION)
                self.assertEqual(response["service_fingerprint"], waitd.SERVICE_FINGERPRINT)
                loop_state = root / "loop.json"
                response = waitctl.request(
                    {
                        "operation": "loop",
                        "argv": [
                            "init",
                            "--state",
                            str(loop_state),
                            "--task",
                            "inspect queue",
                            "--interval",
                            "60",
                            "--session",
                            "session-1",
                        ],
                        "cwd": str(root),
                    },
                    socket_path,
                )
                self.assertEqual(response["output"]["task"], "inspect queue")
                self.assertTrue(waitctl.stop_daemon(socket_path)["ok"])
                process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
