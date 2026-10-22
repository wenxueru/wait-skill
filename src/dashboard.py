"""Serve the loopback DAG dashboard through waitd's goal command queue."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import goal
import loop
import runtime

if TYPE_CHECKING:
    from waitd import WaitDaemon


class Dashboard:
    def __init__(self, daemon: WaitDaemon) -> None:
        self.daemon = daemon
        self.server: asyncio.Server
        self.clients: set[asyncio.Task[None]] = set()
        self.stopping = False
        self.url = ""

    async def start(self) -> str:
        self.server = await asyncio.start_server(self.accept, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def close(self) -> None:
        self.stopping = True
        self.daemon.changed.set()
        self.server.close()
        await self.server.wait_closed()
        tasks = list(self.clients)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self.handle(reader, writer))
        self.clients.add(task)
        task.add_done_callback(self.clients.discard)

    def task(self, mode: str, identity: str) -> dict[str, Any]:
        if mode == "goal":
            if identity not in self.daemon.goals:
                raise ValueError("Goal is not registered")
            graph = goal.GoalStore(Path(identity)).load()
            return {"path": identity, **graph.state, "activity": graph.activity()}
        if mode == "loop":
            if identity not in self.daemon.loops:
                raise ValueError("Loop is not registered")
            state = loop.LoopStore(Path(identity)).load()
            return {"path": identity, **state}

        record = self.daemon.watchers[identity]
        args, _ = runtime.parse_job_args(record["argv"])
        item = {
            **record,
            "label": args.label,
            "event_note": args.event_note,
            "log_file": str(args.log_file),
            "lock_file": str(args.lock_file),
        }
        if args.goal_state or args.loop_state:
            item["owner"] = {
                "mode": "goal" if args.goal_state else "loop",
                "path": str((args.goal_state or args.loop_state).resolve()),
                "node": args.goal_node,
            }
        return item

    def snapshot(self, session: str | None = None) -> dict[str, Any]:
        view = {"goals": [], "loops": [], "watches": [], "linked_watches": []}
        for mode, registry, collection, session_field in (
            ("goal", self.daemon.goals, "goals", "thread"),
            ("loop", self.daemon.loops, "loops", "session"),
            ("wait", self.daemon.watchers, "watches", "session"),
        ):
            for identity in registry:
                if mode == "wait" and session is not None and registry[identity].get("session") != session:
                    continue
                try:
                    item = self.task(mode, identity)
                except (OSError, ValueError) as exc:
                    if session is None:
                        view[collection].append({"path": identity, "error": str(exc)})
                    continue
                if session is not None and item[session_field] != session:
                    continue
                destination = "linked_watches" if mode == "wait" and "owner" in item else collection
                view[destination].append(item)
        return view

    async def action(self, body: dict[str, Any], session: str | None = None) -> dict[str, Any]:
        mode = body.get("mode", "goal")
        if mode not in {"goal", "loop", "wait"}:
            raise ValueError("Unsupported mode")
        key = "watch_id" if mode == "wait" else "path"
        identity = body[key]
        try:
            item = self.task(mode, identity)
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError("Item is not available in this session") from exc
        session_field = "thread" if mode == "goal" else "session"
        if (session is not None and item[session_field] != session) or (mode == "wait" and "owner" in item):
            raise ValueError("Item is not available in this session")
        if mode == "wait":
            if body["action"] != "cancel":
                raise ValueError("Wait supports cancel only")
            return await self.daemon.dispatch({"operation": "cancel", "watch_id": identity})
        if mode == "loop":
            if body["action"] != "cancel":
                raise ValueError("Loop supports cancel only")
            result = await self.daemon.dispatch({
                "operation": "loop",
                "cwd": self.daemon.loops[identity],
                "argv": ["cancel", "--state", identity],
            })
            if result["code"]:
                raise ValueError(result.get("error") or result.get("output"))
            return result

        path = identity
        action = body["action"]
        argv = [action, "--state", path, "--source", "user"]
        if action == "retry":
            argv += ["--id", body["id"]]
        elif action in {"add", "edit"}:
            argv += ["--id", body["id"], "--title", body["title"]]
            for dependency in body.get("depends_on", []):
                argv += ["--depends-on", dependency]
            for criterion in body.get("acceptance", []):
                argv += ["--acceptance", criterion]
            if action == "add":
                argv += ["--kind", body.get("kind", "local")]
                if body.get("read_only"):
                    argv += ["--read-only"]
                for filename in body.get("write_paths", []):
                    argv += ["--write-path", filename]
        elif action not in {"pause", "resume", "cancel"}:
            raise ValueError("Unsupported action")

        result = await self.daemon.dispatch({
            "operation": "goal",
            "argv": argv,
            "cwd": self.daemon.goals[path],
        })
        if result["code"]:
            raise ValueError(result.get("error") or result.get("output"))
        return result

    async def read_request(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, str, dict[str, str]]:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = header.decode("ascii").split("\r\n")
        method, target, _ = lines[0].split()
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key.lower()] = value
        if headers.get("host") != urlsplit(self.url).netloc:
            raise ValueError("Invalid host")
        if headers.get("origin", self.url) != self.url:
            raise ValueError("Invalid origin")
        return method, target, headers

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, headers = await self.read_request(reader)
            address = urlsplit(target)
            query = parse_qs(address.query)
            session = None if query.get("all") == ["1"] else query.get("session", [""])[0]
            target = address.path
            if method == "GET" and target == "/events":
                await self.stream(writer, session)
            elif method == "GET" and target == "/":
                content = (Path(__file__).parents[1] / "assets" / "dashboard.html").read_bytes()
                await self.respond(writer, 200, content, "text/html; charset=utf-8")
            elif method == "GET" and target == "/api/state":
                await self.respond(writer, 200, self.snapshot(session))
            elif method == "POST" and target == "/api/action":
                if headers.get("content-type") != "application/json":
                    raise ValueError("Expected application/json")
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid body length")
                content = await asyncio.wait_for(reader.readexactly(length), 10)
                await self.respond(writer, 200, await self.action(json.loads(content), session))
            else:
                await self.respond(writer, 404, {"error": "Not found"})
        except (
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            with suppress(OSError):
                await self.respond(writer, 400, {"error": str(exc)})
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def stream(self, writer: asyncio.StreamWriter, session: str | None = None) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\r\n")
        while not self.stopping:
            changed = self.daemon.changed
            payload = json.dumps(self.snapshot(session), ensure_ascii=False)
            writer.write(f"data: {payload}\n\n".encode())
            await writer.drain()

            # Heartbeats keep an idle connection alive without rereading the DAG.
            while not changed.is_set() and not self.stopping:
                try:
                    await asyncio.wait_for(changed.wait(), 20)
                except TimeoutError:
                    writer.write(b": keepalive\n\n")
                    await writer.drain()

    @staticmethod
    async def respond(
        writer: asyncio.StreamWriter,
        status: int,
        body: Any,
        content_type: str = "application/json",
    ) -> None:
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        header = (
            f"HTTP/1.1 {status} Response\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(header.encode() + body)
        await writer.drain()
