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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
import waitd  # noqa: E402
import waitctl  # noqa: E402


class WaitDaemonTest(unittest.IsolatedAsyncioTestCase):
    async def test_goal_program_activation_and_wake(self) -> None:
        state = self.root / "goal.json"

        async def goal(operation: str, *arguments: str) -> dict:
            response = await self.daemon.dispatch({
                "operation": "goal", "cwd": str(self.root),
                "argv": [operation, "--state", str(state), *arguments],
            })
            self.assertEqual(response["code"], 0, response)
            return response["output"]

        await goal("init", "--objective", "Verify deployment", "--client", "claude", "--session", "native-test")
        await goal("add", "--id", "deploy", "--title", "Deploy", "--kind", "external")
        await goal("start", "--id", "deploy")
        log, lock, startup = (self.root / name for name in ("watch.json", "watch.lock", "started.json"))
        prepared = await goal("wait", "--id", "deploy", "--label", "deployment",
                              "--log-file", str(log), "--lock-file", str(lock), "--startup-file", str(startup))
        watch_id = prepared["watch_id"]
        submitted = self.daemon.submit([
            "--label", "deployment", "--client", "claude", "--session", "native-test",
            "--event-id", watch_id, "--goal-state", str(state), "--goal-node", "deploy",
            "--log-file", str(log), "--lock-file", str(lock), "--startup-file", str(startup),
            "--activation-interval", ".01", "--notification-timeout", "5",
            "--", sys.executable, "-c", "print('Deployment failed'); raise SystemExit(2)",
        ], str(self.root))
        task = self.daemon.tasks[submitted["watch_id"]]
        deadline = time.monotonic() + 2
        while not startup.exists():
            self.assertLess(time.monotonic(), deadline)
            await asyncio.sleep(.01)
        self.assertFalse(log.exists())  # Program has not started before activation.
        await goal("activate-wait", "--id", "deploy", "--watch-id", watch_id)
        response = await asyncio.wait_for(self.daemon.follow(watch_id), 2)
        self.assertEqual(response["result"]["event"], "exited")
        self.assertEqual(response["result"]["exit_code"], 2)
        self.assertEqual(
            response["resume_message"],
            f"$wait-goal resume {state}; node=deploy; event_id={watch_id}; log_file={log}",
        )
        self.assertEqual(json.loads(state.read_text())["nodes"]["deploy"]["status"], "waiting")
        with lock.open("a") as handle:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        await goal("wake", "--id", "deploy", "--event-id", watch_id, "--event", "exited")
        await asyncio.wait_for(task, 2)
        self.assertEqual(json.loads(state.read_text())["nodes"]["deploy"]["status"], "running")

    async def test_program_result_is_not_a_business_status(self) -> None:
        argv = ["--label", "raw", "--", sys.executable, "-c",
                "import sys; print('{\"status\":\"NotReady\"}'); print('details', file=sys.stderr); sys.exit(42)"]
        submitted = self.daemon.submit(argv, str(self.root))
        await self.daemon.tasks[submitted["watch_id"]]
        record = self.daemon.watchers[submitted["watch_id"]]
        self.assertEqual(record["state"], "completed")  # Program ended; not business success.
        self.assertEqual(record["result"]["event"], "exited")
        self.assertEqual(record["result"]["exit_code"], 42)
        self.assertEqual(record["result"]["stdout"], '{"status":"NotReady"}\n')
        self.assertEqual(record["result"]["stderr"], 'details\n')

    async def test_start_failure_is_available_to_native_callback(self) -> None:
        submitted = self.daemon.submit([
            "--label", "missing", "--client", "claude", "--session", "test",
            "--", str(self.root / "missing-program"),
        ], str(self.root))
        task = self.daemon.tasks[submitted["watch_id"]]
        response = await self.daemon.follow(submitted["watch_id"])
        await task
        self.assertEqual(response["result"]["event"], "start_failed")
        self.assertIn(submitted["log_file"], response["resume_message"])

    async def test_restart_does_not_repeat_an_unconfirmed_program(self) -> None:
        argv = self.watcher_argv("Ready")
        with patch.object(self.daemon, "_schedule"):
            submitted = self.daemon.submit(argv, str(self.root))
        record = self.daemon.watchers[submitted["watch_id"]]
        record["phase"] = "running"
        self.daemon.save()
        restored = waitd.WaitDaemon(self.root / "registry.json")
        with patch.object(waitd, "run_program", side_effect=AssertionError("must not replay")):
            await restored.restore()
            await restored.tasks[submitted["watch_id"]]
        self.assertEqual(restored.watchers[submitted["watch_id"]]["result"]["event"], "interrupted")

    def test_service_rejects_query_flags(self) -> None:
        with self.assertRaises(SystemExit):
            self.daemon.submit(["--label", "x", "--ready", "Ready", "--", "true"], str(self.root))

    async def test_minimal_wait_generates_paths_and_resume_message(self) -> None:
        argv = ["--label", "minimal", "--client", "claude",
                "--session", "native-test", "--", sys.executable, "-c", "print('Ready')"]
        submitted = self.daemon.submit(argv, str(self.root))
        task = self.daemon.tasks[submitted["watch_id"]]
        response = await asyncio.wait_for(self.daemon.follow(submitted["watch_id"]), 2)
        await task
        self.assertEqual(response["result"]["event"], "exited")
        self.assertIn(f"$wait resume {submitted['log_file']};", response["resume_message"])
        self.assertTrue(Path(submitted["log_file"]).is_file())
        self.assertTrue(Path(submitted["lock_file"]).is_file())
        record = self.daemon.watchers[submitted["watch_id"]]
        args, command = waitd.wait_runtime.parse_job_args(record["argv"])
        self.assertEqual(command, argv[argv.index("--") + 1:])
        self.assertEqual(args.event_id, submitted["watch_id"])

    async def test_automatic_lock_rejects_duplicate_without_overwriting_log(self) -> None:
        argv = ["--label", "minimal", "--timeout", "10",
                "--", sys.executable, "-c", "import time; time.sleep(30)"]
        first = self.daemon.submit(argv, str(self.root))
        second = self.daemon.submit(argv, str(self.root))
        self.assertEqual(first["lock_file"], second["lock_file"])
        self.assertNotEqual(first["log_file"], second["log_file"])
        await asyncio.wait_for(self.daemon.tasks[second["watch_id"]], 2)
        self.assertEqual(self.daemon.watchers[second["watch_id"]]["code"], 75)
        self.daemon.cancel(first["watch_id"])
        await self.daemon.tasks[first["watch_id"]]

    async def test_automatic_defaults_preserve_explicit_paths(self) -> None:
        argv = self.watcher_argv("Ready")
        submitted = self.daemon.submit(argv, str(self.root))
        self.assertEqual(submitted["log_file"], str(self.root / "watch.json"))
        args, _ = waitd.wait_runtime.parse_job_args(self.daemon.watchers[submitted["watch_id"]]["argv"])
        self.assertEqual(str(args.lock_file), submitted["lock_file"])
        await self.daemon.tasks[submitted["watch_id"]]

    async def test_native_goal_wait_retains_lock_until_ack_or_delivery_deadline(self) -> None:
        for acknowledge in (False, True):
            with self.subTest(acknowledge=acknowledge):
                argv = self.watcher_argv("Ready")
                argv[:0] = ["--client", "claude", "--session", "native-test"]
                args, command = waitd.wait_runtime.parse_job_args(argv)
                args.goal_state = self.root / "goal.json"
                args.wake_ack_timeout = .001
                args.notification_timeout = .15
                args.activation_interval = .005
                available_at = time.time()
                result = {"event": "exited", "notification": "native_pending", "available_at": available_at}
                record = {"watch_id": "native", "argv": argv, "state": "active",
                          "phase": "notifying", "code": 0, "result": result}
                self.daemon.watchers["native"] = record
                with patch.object(waitd.wait_runtime, "goal_wait_is_current", return_value=True) as current:
                    task = asyncio.create_task(self.daemon._watch(args, command, record, asyncio.Event()))
                    await asyncio.sleep(.03)
                    self.assertFalse(task.done())
                    with args.lock_file.open("a") as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(record["wake_ack_deadline_at"], available_at + .15)
                    if acknowledge:
                        response = await self.daemon.follow("native")
                        self.assertEqual(response["result"]["event"], "exited")
                        current.return_value = False
                    _, code = await asyncio.wait_for(task, 1)
                self.assertEqual(code, 0 if acknowledge else waitd.wait_runtime.EXIT_NOTIFY_FAILED)
                self.assertEqual(result["notification"], "native_pending" if acknowledge else "unconfirmed")
                self.assertEqual(json.loads(args.log_file.read_text())["event"], "exited")

    async def test_follow_disconnect_cancels_pending_request(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_request(request: object) -> dict[str, object]:
            started.set()
            try:
                return await asyncio.Future()
            finally:
                cancelled.set()

        reader = asyncio.StreamReader()
        reader.feed_data(b'{"operation":"follow","watch_id":"test","timeout":60}\n')
        writer = Mock(wait_closed=AsyncMock())
        with patch.object(self.daemon, "dispatch", side_effect=pending_request):
            client = asyncio.create_task(waitd.handle_client(self.daemon, reader, writer))
            await asyncio.wait_for(started.wait(), 1)
            reader.feed_eof()
            await asyncio.wait_for(client, 1)
        self.assertTrue(cancelled.is_set())
        writer.write.assert_not_called()
        writer.close.assert_called_once()

    async def test_native_follow_receives_event_without_spawning_resume(self) -> None:
        argv = self.watcher_argv("Ready")
        argv[0:0] = ["--client", "claude", "--session", "native-test"]
        with patch.object(waitd.wait_runtime, "notification_command", side_effect=AssertionError("must not resume")):
            submitted = self.daemon.submit(argv, str(self.root))
            task = self.daemon.tasks[submitted["watch_id"]]
            response = await self.daemon.dispatch({"operation": "follow", "watch_id": submitted["watch_id"], "timeout": 2})
            await task
        self.assertEqual(response["result"]["event"], "exited")
        self.assertEqual(response["result"]["notification"], "native_pending")
        self.assertIn(submitted["watch_id"], response["resume_message"])
        self.assertEqual(json.loads((self.root / "watch.json").read_text())["notification"], "native_pending")
        repeated = await self.daemon.follow(submitted["watch_id"])
        self.assertEqual(repeated["result"], response["result"])

    async def test_follow_timeout_does_not_cancel_watcher(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Waiting"), str(self.root))
        try:
            with self.assertRaisesRegex(ValueError, "follow timed out"):
                await self.daemon.dispatch({"operation": "follow", "watch_id": submitted["watch_id"], "timeout": .02})
            self.assertEqual(self.daemon.watchers[submitted["watch_id"]]["state"], "active")
        finally:
            self.daemon.cancel(submitted["watch_id"])
            await self.daemon.tasks[submitted["watch_id"]]

    async def test_follow_reports_cancellation(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Waiting"), str(self.root))
        follower = asyncio.create_task(self.daemon.follow(submitted["watch_id"]))
        await asyncio.sleep(0)
        self.daemon.cancel(submitted["watch_id"])
        self.assertEqual((await asyncio.wait_for(follower, 1))["state"], "cancelled")
        await self.daemon.tasks[submitted["watch_id"]]

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.daemon = waitd.WaitDaemon(self.root / "registry.json")

    def watcher_argv(self, status: str) -> list[str]:
        return [
            "--label",
            "demo",
            "--timeout",
            "2",
            "--lock-file",
            str(self.root / "watch.lock"),
            "--log-file",
            str(self.root / "watch.json"),
            "--",
            sys.executable,
            "-c",
            f"import time; time.sleep({30 if status == 'Waiting' else 0}); print({status!r})",
        ]

    async def test_cancelled_program_is_reaped_before_lock_release(self) -> None:
        pid_file = self.root / "program.pid"
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

    async def test_program_output_is_bounded_and_timeout_is_reported(self) -> None:
        result = await waitd.run_program([sys.executable, "-c", "print('x'*100000)"], 2, str(self.root))
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(result["stdout"]), waitd.MAX_PROGRAM_OUTPUT_BYTES)
        result = await waitd.run_program([sys.executable, "-c", "import time; time.sleep(30)"], .05, str(self.root))
        self.assertEqual(result["event"], "timeout")
        self.assertIsNone(result["exit_code"])

    async def test_activation_timeout_writes_log(self) -> None:
        args, command = waitd.wait_runtime.parse_job_args(self.watcher_argv("Ready"))
        args.goal_state = self.root / "goal.json"
        args.goal_node = "deploy"
        args.startup_file = self.root / "started.json"
        args.event_id = "activation"
        record = {"watch_id": "activation", "phase": "activating", "state": "active",
                  "deadline_at": time.time()+1, "submitted_at": time.time(),
                  "activation_deadline_at": time.time()-1}
        with patch.object(waitd.wait_runtime, "goal_wait_phase", return_value="prepared"):
            result, code = await self.daemon._watch(args, command, record, asyncio.Event())
        self.assertEqual(code, 76)
        self.assertEqual(result["event"], "activation_timeout")
        self.assertEqual(json.loads(args.log_file.read_text()), result)

    async def test_delivery_launch_failure_is_persisted(self) -> None:
        args, _ = waitd.wait_runtime.parse_job_args(self.watcher_argv("Ready"))
        args.thread = "session"
        result = {"event_id": "delivery", "event": "exited", "status": "Ready"}
        record = {"watch_id": "delivery", "cwd": str(self.root), "state": "active"}
        with patch.object(asyncio, "create_subprocess_exec", side_effect=FileNotFoundError):
            code = await self.daemon._deliver(args, result, record, asyncio.Event())
        self.assertEqual(code, 70)
        self.assertEqual(json.loads(args.log_file.read_text())["notification"], "failed")

    def test_resume_instruction_matches_mode(self) -> None:
        for mode in ("wait", "wait-loop", "wait-goal"):
            with self.subTest(mode=mode):
                target = "log.json" if mode == "wait" else "state.json"
                argv = ["--label", "x", "--log-file", "log.json",
                        "--lock-file", "lock", "--session", "session", "--event-id", "event"]
                if mode != "wait":
                    argv += ["--loop-state" if mode == "wait-loop" else "--goal-state", target]
                if mode == "wait-goal":
                    argv += ["--goal-node", "n", "--startup-file", "started.json"]
                argv += ["--", "true"]
                args, _ = waitd.wait_runtime.parse_job_args(waitd.absolute_wait_argv(argv, self.root))
                message = waitd.wait_runtime.resume_instruction(args)
                if mode == "wait":
                    self.assertEqual(message, f"$wait resume {args.log_file}; event_id={args.event_id}")
                elif mode == "wait-loop":
                    self.assertEqual(
                        message,
                        f"$wait-loop resume {args.loop_state}; event_id={args.event_id}; log_file={args.log_file}",
                    )
                else:
                    self.assertEqual(
                        message,
                        f"$wait-goal resume {args.goal_state}; node={args.goal_node}; "
                        f"event_id={args.event_id}; log_file={args.log_file}",
                    )

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
            self.assertEqual(self.daemon.watchers[watch_id]["result"]["event"], "exited")
            self.assertEqual(self.daemon.watchers[watch_id]["result"]["stdout"], "Ready\n")
            args, _ = waitd.wait_runtime.parse_job_args(self.daemon.watchers[watch_id]["argv"])
            self.assertEqual(
                waitd.wait_runtime.resume_instruction(args),
                f"$wait-loop resume {path.resolve()}; event_id={args.event_id}; log_file={args.log_file}",
            )
            deliver.assert_awaited_once()

    async def test_submit_runs_watcher_and_persists_result(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Ready"), str(self.root))
        watch_id = str(submitted["watch_id"])
        await asyncio.wait_for(self.daemon.tasks[watch_id], timeout=2)

        record = self.daemon.watchers[watch_id]
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["result"]["event"], "exited")
        self.assertEqual(json.loads((self.root / "watch.json").read_text())["event_id"], watch_id)
        await asyncio.sleep(0)
        self.assertNotIn(watch_id, self.daemon.tasks)

    async def test_cancel_interrupts_program_and_releases_lock(self) -> None:
        submitted = self.daemon.submit(self.watcher_argv("Waiting"), str(self.root))
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
            "event": "exited",
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

        self.assertEqual(code, waitd.wait_runtime.EXIT_NOTIFY_FAILED)
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
            "event": "exited",
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
        runner = waitd.StateCommandRunner()
        await runner.locks["goal"].acquire()
        original_timeout = waitd.STATE_COMMAND_TIMEOUT
        waitd.STATE_COMMAND_TIMEOUT = 0.01
        try:
            result = await asyncio.wait_for(
                runner.run("goal", ["check"], self.root),
                timeout=0.1,
            )
        finally:
            waitd.STATE_COMMAND_TIMEOUT = original_timeout
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
    @contextmanager
    def spawn_waitd(self, root: Path) -> Iterator[tuple[Path, subprocess.Popen[str]]]:
        socket_path = root / "waitd.sock"
        process = subprocess.Popen(
            [
                sys.executable,
                str(SRC / "waitd.py"),
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
                    waitctl.request({"operation": "ping"}, socket_path)
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
            yield socket_path, process
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=2)

    def test_unix_socket_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.spawn_waitd(root) as (socket_path, process):
                response = waitctl.request({"operation": "ping"}, socket_path)
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
                submitted = waitctl.request({
                    "operation": "submit", "cwd": str(root),
                    "argv": ["--label", "socket-test", "--timeout", "2",
                             "--", sys.executable, "-c", "import time; time.sleep(.1); print('Ready')"],
                }, socket_path)
                followed = waitctl.request({"operation": "follow", "watch_id": submitted["watch_id"], "timeout": 2}, socket_path)
                self.assertTrue(followed["ok"])
                self.assertEqual(followed["result"]["event"], "exited")
                self.assertTrue(waitctl.stop_daemon(socket_path)["ok"])
                process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0)

    def test_goal_wait_program_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.spawn_waitd(root) as (socket_path, _process):
                def goal(*argv: str) -> dict[str, object]:
                    return waitctl.request(
                        {"operation": "goal", "argv": list(argv), "cwd": str(root)},
                        socket_path,
                        timeout=35.0,
                    )

                state = root / "goal.json"
                goal("init", "--state", str(state), "--objective", "verify",
                     "--client", "claude", "--session", "e2e")
                goal("add", "--state", str(state), "--id", "deploy", "--title", "Deploy", "--kind", "external")
                goal("start", "--state", str(state), "--id", "deploy")
                response = goal(
                    "wait", "--state", str(state), "--id", "deploy", "--label", "deployment",
                    "--timeout", "10", "--", sys.executable, "-c", "print('deploy done')",
                )
                self.assertEqual(response["code"], 0, response)
                prepared = response["output"]
                self.assertTrue(prepared["submitted"])
                self.assertEqual(prepared["receipt"], "verified")
                watch_id = str(prepared["watch_id"])

                # The state command submitted the watcher back through the daemon socket.
                listing = waitctl.request({"operation": "list"}, socket_path)
                self.assertIn(watch_id, [w["watch_id"] for w in listing["watchers"]])

                goal("activate-wait", "--state", str(state), "--id", "deploy", "--watch-id", watch_id)
                followed = waitctl.request(
                    {"operation": "follow", "watch_id": watch_id, "timeout": 5}, socket_path,
                )
                self.assertEqual(followed["result"]["event"], "exited")
                self.assertEqual(followed["result"]["exit_code"], 0)

                goal("wake", "--state", str(state), "--id", "deploy", "--event-id", watch_id, "--event", "exited")
                shown = goal("show", "--state", str(state))
                self.assertEqual(shown["output"]["nodes"]["deploy"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
