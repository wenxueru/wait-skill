from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import waitctl
from tests import SRC
from wait.service import daemon as waitd


class WaitServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.socket_path = self.root / "waitd.sock"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(SRC / "worker.py"),
                "daemon",
                "serve",
                "--socket",
                str(self.socket_path),
                "--registry",
                str(self.root / "registry.json"),
            ],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_process)
        deadline = time.monotonic() + 2
        while True:
            try:
                self.request("ping")
                return
            except OSError:
                if self.process.poll() is not None:
                    error = self.process.communicate()[1]
                    if "Operation not permitted" in error:
                        self.skipTest("sandbox does not permit Unix sockets")
                    self.fail(f"waitd exited during startup: {error.strip()}")
                if time.monotonic() >= deadline:
                    self.fail("waitd did not create its control socket")
                time.sleep(0.01)

    def stop_process(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.process.communicate(timeout=2)

    def request(self, operation: str, **fields: object) -> dict[str, object]:
        response = waitctl.request({"operation": operation, "cwd": str(self.root), **fields}, self.socket_path, timeout=35)
        self.assertTrue(response["ok"], response)
        return response

    def goal(self, operation: str, *argv: str) -> dict[str, object]:
        response = self.request("goal", argv=[operation, "--state", str(self.root / "goal.json"), *argv])
        self.assertEqual(response["code"], 0, response)
        return response["output"]

    def test_unix_socket_control_plane(self) -> None:
        response = self.request("ping")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["protocol_version"], waitd.PROTOCOL_VERSION)
        self.assertEqual(response["service_fingerprint"], waitd.SERVICE_FINGERPRINT)
        response = self.request(
            "loop",
            argv=[
                "init",
                "--state",
                str(self.root / "loop.json"),
                "--task",
                "inspect queue",
                "--event-note",
                "Inspect the queue again",
                "--interval",
                "60",
                "--session",
                "session-1",
            ],
        )
        self.assertEqual(response["output"]["task"], "inspect queue")
        submitted = self.request(
            "submit",
            argv=[
                "--label",
                "socket-test",
                "--event-note",
                "Check the result",
                "--timeout",
                "2",
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(.1); print('Ready')",
            ],
        )
        followed = self.request("follow", watch_id=submitted["watch_id"], timeout=2)
        self.assertEqual(followed["result"]["event"], "exited")
        self.assertEqual(followed["result"]["exit_code"], 0)
        self.assertEqual(followed["result"]["stdout"].strip(), "Ready")
        self.assertTrue(waitctl.stop_daemon(self.socket_path)["ok"])
        self.process.communicate(timeout=2)
        self.assertEqual(self.process.returncode, 0)

    def test_goal_wait_program_round_trip(self) -> None:
        self.goal("init", "--objective", "verify", "--client", "claude", "--session", "e2e")
        self.goal("add", "--id", "deploy", "--title", "Deploy", "--kind", "external")
        self.goal("start", "--id", "deploy")
        prepared = self.goal(
            "wait",
            "--id",
            "deploy",
            "--label",
            "deployment",
            "--event-note",
            "Recheck deployment and continue",
            "--timeout",
            "10",
            "--",
            sys.executable,
            "-c",
            "print('deploy done')",
        )
        self.assertTrue(prepared["submitted"])
        self.assertEqual(prepared["receipt"], "verified")
        watch_id = str(prepared["watch_id"])
        listing = self.request("list")
        self.assertIn(watch_id, [watch["watch_id"] for watch in listing["watchers"]])
        self.goal("activate-wait", "--id", "deploy", "--watch-id", watch_id)
        followed = self.request("follow", watch_id=watch_id, timeout=5)
        self.assertEqual(followed["result"]["event"], "exited")
        self.assertEqual(followed["result"]["exit_code"], 0)
        self.goal("wake", "--id", "deploy", "--event-id", watch_id, "--event", "exited")
        self.assertEqual(self.goal("show")["nodes"]["deploy"]["status"], "running")
