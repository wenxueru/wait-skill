from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import waitctl
from wait.service.daemon import WaitDaemon
from wait.service.dashboard import Dashboard
from wait.tasks.loop import timer_argv


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
    async def http(self, target="/api/state", body=None, origin=None, host=None, extra_headers=()):
        if "?" not in target:
            target += "?all=1"
        address = urlsplit(self.dashboard.url)
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        authority = address.netloc if host is None else host
        content = json.dumps(body).encode() if body is not None else b""
        method = "POST" if body is not None else "GET"
        request = f"{method} {target} HTTP/1.1\r\n"
        if authority:
            request += f"Host: {authority}\r\n"
        if origin is not None:
            request += f"Origin: {origin}\r\n"
        for key, value in extra_headers:
            request += f"{key}: {value}\r\n"
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

    async def test_session_links_preserve_registered_working_directory(self):
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

    async def test_rejects_unregistered_path(self):
        code, _ = await self.response(target="/api/action", body={"path": "/tmp/unknown.json", "action": "pause"})
        self.assertEqual(code, 400)
        code, payload = await self.response()
        self.assertEqual(code, 200)
        self.assertEqual(payload["goals"][0]["objective"], "Dashboard test")

    async def test_forwarded_loopback_html_api_actions_and_live_events(self):
        for host, origin in (
            ("localhost:63671", "http://localhost:63671"),
            ("127.0.0.1:63671", "http://127.0.0.1:63671"),
            ("[::1]:63671", "http://[::1]:63671"),
            ("LOCALHOST:63671", "http://localhost:63671"),
            ("localhost:80", "http://localhost"),
        ):
            with self.subTest(host=host):
                code, state = await self.response(target="/api/state?session=session-a", host=host, origin=origin)
                self.assertEqual(code, 200)
                self.assertEqual([goal["thread"] for goal in state["goals"]], ["session-a"])

        headers = {"host": "localhost:63671", "origin": "http://localhost:63671"}
        async with self.http("/?session=session-a", host=headers["host"]) as reader:
            response = await asyncio.wait_for(reader.read(), 2)
        header, html = response.split(b"\r\n\r\n", 1)
        self.assertEqual(header.split()[1], b"200")
        self.assertIn(b"text/html", header)
        self.assertIn(b"<!doctype html>", html)
        for asset in ("dashboard.mjs", "appearance.mjs", "task-model.mjs", "graph-layout.mjs", "vendor/dagre.mjs"):
            async with self.http(f"/assets/{asset}", **headers) as reader:
                response = await asyncio.wait_for(reader.read(), 2)
            header, script = response.split(b"\r\n\r\n", 1)
            self.assertEqual(header.split()[1], b"200")
            self.assertIn(b"text/javascript", header)
            self.assertTrue(script)
        for target in ("/assets/../SKILL.md", "/assets/vendor/unknown.mjs"):
            code, _ = await self.response(target=target, **headers)
            self.assertEqual(code, 404)
        code, state = await self.response(
            target="/api/state?session=session-b",
            **headers,
            extra_headers=(
                ("Accept", "application/json"),
                ("Accept", "*/*"),
                ("X-Forwarded-For", "192.0.2.1"),
                ("X-Forwarded-For", "127.0.0.1"),
            ),
        )
        self.assertEqual(code, 200)
        self.assertFalse(state["goals"])

        async with (
            self.http("/events?session=session-a", **headers) as current,
            self.http("/events?session=session-b", **headers) as other,
        ):
            for reader in (current, other):
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                self.assertIn(b"text/event-stream", header)
            self.assertIn(b'"status": "open"', await asyncio.wait_for(current.readuntil(b"\n\n"), 2))
            self.assertIn(b'"goals": []', await asyncio.wait_for(other.readuntil(b"\n\n"), 2))
            code, _ = await self.response(
                target="/api/action?session=session-a",
                body={"path": str(self.path), "action": "pause"},
                **headers,
            )
            self.assertEqual(code, 200)
            self.assertIn(b'"status": "paused"', await asyncio.wait_for(current.readuntil(b"\n\n"), 2))
            self.assertIn(b'"goals": []', await asyncio.wait_for(other.readuntil(b"\n\n"), 2))
        code, _ = await self.response(
            target="/api/action?session=session-b",
            body={"path": str(self.path), "action": "resume"},
            **headers,
        )
        self.assertEqual(code, 400)
        self.assertEqual(self.dashboard.snapshot()["goals"][0]["status"], "paused")

    async def test_rejects_non_loopback_hosts_and_cross_site_origins_on_every_route(self):
        invalid_hosts = (
            "",
            "example.com:63671",
            "192.168.1.2:63671",
            "0.0.0.0:63671",
            "[::]:63671",
            "localhost.evil:63671",
            "127.0.0.1.evil:63671",
            "localhost@evil:63671",
            "evil@localhost:63671",
            "localhost:0",
            "localhost:65536",
            "localhost:abc",
            "localhost:",
            "localhost:63671/path",
            "localhost:63671?x",
            "localhost:63671#x",
        )
        invalid_origins = (
            "https://example.com",
            "http://evil:63671",
            "null",
            "",
            "http://localhost:63670",
            "http://127.0.0.1:63671",
            "https://localhost:63671",
            "http://localhost:63671/",
            "http://localhost:63671/path",
            "http://evil@localhost:63671",
            "http://localhost:63671#x",
        )
        body = {"path": str(self.path), "action": "cancel"}
        for target in ("/", "/api/state", "/events", "/api/action"):
            for fields in ({"host": "evil:63671"}, {"host": "localhost:63671", "origin": "http://localhost:63670"}):
                with self.subTest(target=target, fields=fields):
                    code, _ = await self.response(target=target, body=body if target == "/api/action" else None, **fields)
                    self.assertEqual(code, 400)

        invalid_requests = [{"host": host} for host in invalid_hosts]
        invalid_requests.extend({"host": "localhost:63671", "origin": origin} for origin in invalid_origins)
        for key, value in (
            ("hOsT", "localhost:63671"),
            ("oRiGiN", "http://localhost:63671"),
            ("Content-Length", "0"),
            ("Content-Type", "text/plain"),
        ):
            invalid_requests.append({
                "host": "localhost:63671",
                "origin": "http://localhost:63671",
                "extra_headers": ((key, value),),
            })
        invalid_requests.append({
            "host": "evil:63671",
            "extra_headers": (("X-Forwarded-Host", "localhost:63671"), ("Forwarded", "host=localhost:63671")),
        })
        for fields in invalid_requests:
            with self.subTest(fields=fields):
                code, _ = await self.response(target="/api/action", body=body, **fields)
                self.assertEqual(code, 400)
        self.assertEqual(self.dashboard.snapshot()["goals"][0]["status"], "open")


class DashboardUrlTest(unittest.TestCase):
    def test_forwarded_url_preserves_scope_without_changing_daemon_address(self):
        original = "http://127.0.0.1:35857/?session=session-a&mode=goal&focus=%2Ftmp%2Fa.json&node=first"
        for base in (None, "http://localhost:63671/", "http://127.0.0.1:63671", "http://[::1]:63671", "http://localhost"):
            with self.subTest(base=base):
                response = {"ok": True, "url": original}
                output = io.StringIO()
                with (
                    patch.object(waitctl, "start_daemon"),
                    patch.object(waitctl, "request", return_value=response) as request,
                    patch("sys.stdout", output),
                ):
                    args = ["dashboard", "--session", "session-a"]
                    if base:
                        args += ["--base-url", base]
                    self.assertEqual(waitctl.main(args), 0)
                url = urlsplit(json.loads(output.getvalue())["url"])
                expected = "localhost:80" if base == "http://localhost" else urlsplit(base or original).netloc
                self.assertEqual(url.netloc, expected)
                self.assertEqual((url.path, url.query), (urlsplit(original).path, urlsplit(original).query))
                self.assertNotIn("base_url", request.call_args.args[0])

        for base in (
            "https://localhost:63671",
            "http://example.com",
            "http://localhost:0",
            "http://localhost:63671/?all=1",
            "http://localhoſt:63671",
            "http://user@localhost:63671",
        ):
            with self.subTest(base=base), patch.object(waitctl, "start_daemon") as start, patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    waitctl.main(["dashboard", "--session", "session-a", "--base-url", base])
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
