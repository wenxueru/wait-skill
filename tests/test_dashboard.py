from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from dashboard import Dashboard
from loop import timer_argv
from waitd import WaitDaemon


class DashboardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = await asyncio.to_thread(Path(self.tmp.name).resolve)
        self.daemon = WaitDaemon(self.root / "registry.json", self.root / "waitd.sock")
        self.dashboard = Dashboard(self.daemon)
        await self.dashboard.start()
        self.path = self.root / "goal.json"
        await self.command("init", "--objective", "Dashboard test", "--session", "session-a")
        await self.command("add", "--id", "a", "--title", "First", "--read-only")
        await self.command("add", "--id", "b", "--title", "Second", "--depends-on", "a", "--read-only")

    async def asyncTearDown(self):
        await self.dashboard.close()
        self.tmp.cleanup()

    async def command(self, action, *args, mode="goal", path=None):
        result = await self.daemon.dispatch({
            "operation": mode,
            "cwd": str(self.root),
            "argv": [action, "--state", str(path or self.path), *args],
        })
        self.assertEqual(result["code"], 0, result)
        return result

    @asynccontextmanager
    async def http(self, target="/api/state", body=None, origin=None):
        if "?" not in target:
            target += "?all=1"
        address = urlsplit(self.dashboard.url)
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        authority = address.netloc
        content = json.dumps(body).encode() if body is not None else b""
        method = "POST" if body is not None else "GET"
        request = f"{method} {target} HTTP/1.1\r\nHost: {authority}\r\n"
        if origin:
            request += f"Origin: {origin}\r\n"
        if body is not None:
            request += f"Content-Type: application/json\r\nContent-Length: {len(content)}\r\n"
        writer.write(request.encode() + b"\r\n" + content)
        await writer.drain()
        try:
            yield reader
        finally:
            writer.close()
            await writer.wait_closed()

    async def response(self, **kwargs):
        async with self.http(**kwargs) as reader:
            result = await asyncio.wait_for(reader.read(), 2)
        header, body = result.split(b"\r\n\r\n", 1)
        return int(header.split()[1]), json.loads(body)

    async def action(self, action, **fields):
        return await self.response(target="/api/action", body={"path": str(self.path), "action": action, **fields})

    async def test_edit_and_cycle_rollback(self):
        code, response = await self.action("edit", id="b", title="Updated", depends_on=["a"], acceptance=["Verified"])
        self.assertEqual(code, 200, response)
        saved = self.path.read_text()
        state = json.loads(saved)
        self.assertEqual(state["events"][-1]["source"], "user")
        node = state["nodes"]["b"]
        self.assertEqual((node["title"], node["depends_on"], node["acceptance"]), ("Updated", ["a"], ["Verified"]))
        code, _ = await self.action("edit", id="a", title="Cycle", depends_on=["b"])
        self.assertEqual(code, 400)
        self.assertEqual(self.path.read_text(), saved)
        await self.command("start", "--id", "a")
        code, _ = await self.action("edit", id="a", title="Running")
        self.assertEqual(code, 400)

    async def test_controls_retry_add_and_registry(self):
        for action, status in [("pause", "paused"), ("resume", "open")]:
            code, _ = await self.action(action)
            self.assertEqual(code, 200)
            self.assertEqual(self.dashboard.snapshot()["goals"][0]["status"], status)
        await self.command("start", "--id", "a")
        await self.command("fail", "--id", "a", "--summary", "Test failure")
        self.assertEqual((await self.action("retry", id="a"))[0], 200)
        self.assertEqual((await self.action("add", id="c", title="Third", read_only=True, depends_on=["b"]))[0], 200)
        nodes = self.dashboard.snapshot()["goals"][0]["nodes"]
        self.assertEqual(nodes["a"]["status"], "pending")
        self.assertEqual(nodes["c"]["depends_on"], ["b"])
        self.assertEqual((await self.action("cancel"))[0], 200)
        self.assertEqual(self.dashboard.snapshot()["goals"][0]["status"], "cancelled")
        restored = WaitDaemon(self.root / "registry.json", self.root / "other.sock")
        self.assertIn(str(self.path), restored.goals)

    async def test_two_sessions_keep_goal_changes_separate(self):
        other = self.root / "other-goal.json"
        await self.command("init", "--objective", "Second goal", "--session", "session-b", path=other)
        responses = await asyncio.gather(
            self.action("pause"),
            self.response(
                target="/api/action",
                body={
                    "path": str(other),
                    "action": "add",
                    "id": "a",
                    "title": "Other session task",
                    "read_only": True,
                },
            ),
        )
        self.assertEqual([code for code, _ in responses], [200, 200])
        _, payload = await self.response()
        goals = {goal["thread"]: goal for goal in payload["goals"]}
        self.assertEqual(goals["session-a"]["status"], "paused")
        self.assertEqual(goals["session-b"]["status"], "open")
        self.assertEqual(goals["session-a"]["nodes"]["a"]["title"], "First")
        self.assertEqual(goals["session-b"]["nodes"]["a"]["title"], "Other session task")

    async def test_scoped_loop_and_goal_views_and_controls(self):
        watches = {}
        loops = {}
        for session in ("session-a", "session-b"):
            state = self.root / f"{session}-loop.json"
            await self.command(
                "init",
                "--task",
                session + " loop",
                "--interval",
                "60",
                "--event-note",
                "Continue the test loop",
                "--session",
                session,
                "--client",
                "codex",
                "--no-notify",
                mode="loop",
                path=state,
            )
            loops[session] = str(state)
            with patch.object(self.daemon, "_schedule"):
                await self.command("complete", "--summary", "First iteration done", mode="loop", path=state)
                submitted = self.daemon.submit(
                    [
                        "--client",
                        "codex",
                        "--session",
                        session,
                        "--no-notify",
                        "--label",
                        session + " wait",
                        "--timeout",
                        "60",
                        "--",
                        sys.executable,
                        "-c",
                        "pass",
                    ],
                    str(self.root),
                )
            watches[session] = submitted["watch_id"]

        code, state = await self.response(target="/api/state?session=session-a")
        self.assertEqual(code, 200)
        self.assertEqual([g["thread"] for g in state["goals"]], ["session-a"])
        self.assertEqual([w["watch_id"] for w in state["watches"]], [watches["session-a"]])
        self.assertEqual([loop["path"] for loop in state["loops"]], [loops["session-a"]])
        self.assertTrue(state["loops"][0]["no_notify"])
        self.assertIn("--no-notify", timer_argv(state["loops"][0], Path(loops["session-a"])))
        _, blank = await self.response(target="/api/state?session=")
        self.assertTrue(all(not rows for rows in blank.values()))

        for mode, identity in (
            ("wait", {"watch_id": watches["session-b"]}),
            ("loop", {"path": loops["session-b"]}),
        ):
            code, _ = await self.response(
                target="/api/action?session=session-a",
                body={
                    "mode": mode,
                    "action": "cancel",
                    **identity,
                },
            )
            self.assertEqual(code, 400)
        for mode, identity in (
            ("wait", {"watch_id": watches["session-a"]}),
            ("loop", {"path": loops["session-a"]}),
        ):
            code, _ = await self.response(
                target="/api/action?session=session-a",
                body={
                    "mode": mode,
                    "action": "cancel",
                    **identity,
                },
            )
            self.assertEqual(code, 200)
        _, state = await self.response(target="/api/state?all=1")
        self.assertEqual(
            {w["session"]: w["state"] for w in state["watches"]}, {"session-a": "cancelled", "session-b": "active"}
        )
        self.assertEqual(
            {loop["session"]: loop["status"] for loop in state["loops"]}, {"session-a": "cancelled", "session-b": "active"}
        )

    async def test_session_links_and_sse_updates(self):
        self.daemon.dashboard_url = self.dashboard.url
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.daemon.register_goal(self.path, workspace)
        for request in (
            {"operation": "dashboard", "session": "session-a"},
            {"operation": "dashboard", "state": str(self.path)},
        ):
            result = await self.daemon.dispatch(request)
            self.assertTrue(result["url"].endswith("/?session=session-a"))
        self.assertEqual(self.daemon.goals[str(self.path)], str(workspace))
        result = await self.command("show")
        self.assertIn("session=session-a", result["dashboard_url"])
        self.assertIn("mode=goal", result["dashboard_url"])
        self.assertIn("focus=", result["dashboard_url"])
        with self.assertRaises(ValueError):
            await self.daemon.dispatch({"operation": "dashboard"})

        async with self.http("/events?session=session-a") as current, self.http("/events?session=session-b") as other:
            for reader in (current, other):
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            self.assertIn(b"Dashboard test", await asyncio.wait_for(current.readuntil(b"\n\n"), 2))
            self.assertIn(b'"goals": []', await asyncio.wait_for(other.readuntil(b"\n\n"), 2))
            await self.command("pause")
            self.assertIn(b'"status": "paused"', await asyncio.wait_for(current.readuntil(b"\n\n"), 2))
            self.assertNotIn(b"Dashboard test", await asyncio.wait_for(other.readuntil(b"\n\n"), 2))

        code, _ = await self.response(
            target="/api/action?session=session-b",
            body={
                "path": str(self.path),
                "action": "resume",
            },
        )
        self.assertEqual(code, 400)

    async def test_rejects_unregistered_path_and_cross_origin(self):
        code, _ = await self.response(target="/api/action", body={"path": "/tmp/unknown.json", "action": "pause"})
        self.assertEqual(code, 400)
        code, _ = await self.response(
            target="/api/action", body={"path": str(self.path), "action": "pause"}, origin="https://example.com"
        )
        self.assertEqual(code, 400)
        code, payload = await self.response()
        self.assertEqual(code, 200)
        self.assertEqual(payload["goals"][0]["objective"], "Dashboard test")


if __name__ == "__main__":
    unittest.main()
