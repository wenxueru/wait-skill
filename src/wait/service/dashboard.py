"""Serve the loopback DAG dashboard through waitd's goal command queue."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .. import runtime
from ..http import loopback_origin
from ..tasks import goal, loop

if TYPE_CHECKING:
    from .daemon import WaitDaemon


MAX_ACTION_BYTES = 64 * 1024
dashboard_json = partial(json.dumps, ensure_ascii=False)


def request_session(request: web.Request) -> str | None:
    return None if request.query.getall("all", []) == ["1"] else request.query.get("session", "")


@web.middleware
async def loopback_only(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        headers = request.headers
        for name in ("Host", "Origin", "Content-Type", "Content-Length"):
            if len(headers.getall(name, ())) > 1:
                raise ValueError("Duplicate header")
        authority = loopback_origin(headers.get("Host", ""))
        origin = headers.get("Origin")
        if origin is not None and (not origin.startswith("http://") or loopback_origin(origin[7:]) != authority):
            raise ValueError("Invalid origin")
        return await handler(request)
    except web.HTTPException as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    except (TimeoutError, ValueError, KeyError, TypeError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def serve_asset(path: Path, content_type: str, request: web.Request) -> web.FileResponse:
    return web.FileResponse(path, headers={"Content-Type": f"{content_type}; charset=utf-8"})


class Dashboard:
    def __init__(self, daemon: WaitDaemon) -> None:
        self.daemon = daemon
        assets = Path(__file__).resolve().parents[3] / "assets"
        dashboard = Path(__file__).resolve().parents[1] / "dashboard"
        self.stopping = False
        self.url = ""
        app = web.Application(middlewares=[loopback_only], client_max_size=MAX_ACTION_BYTES + 1)
        app.on_response_prepare.append(self.cache_headers)
        app.router.add_get("/api/state", self.state_response)
        app.router.add_get("/api/sessions", self.sessions_response)
        app.router.add_get("/events", self.stream, allow_head=False)
        app.router.add_post("/api/action", self.action_response)
        app.router.add_get("/", partial(serve_asset, dashboard / "index.html", "text/html"))
        app.router.add_get("/assets/dashboard/styles.css", partial(serve_asset, dashboard / "styles.css", "text/css"))
        for script in (
            "app.mjs",
            "appearance.mjs",
            "controls/session-picker.mjs",
            "controls/status-filter.mjs",
            "controls/status-help.mjs",
            "controls/viewport.mjs",
            "graph/layout.mjs",
            "models/activity.mjs",
            "models/links.mjs",
            "models/tasks.mjs",
        ):
            app.router.add_get(f"/assets/dashboard/{script}", partial(serve_asset, dashboard / script, "text/javascript"))
        for script in ("dagre.mjs", "ui.mjs"):
            app.router.add_get(f"/assets/vendor/{script}", partial(serve_asset, assets / "vendor" / script, "text/javascript"))
        for icon in ("icon.svg", "logo.svg"):
            app.router.add_get(f"/assets/brand/{icon}", partial(serve_asset, assets / "brand" / icon, "image/svg+xml"))
        self.runner = web.AppRunner(app, shutdown_timeout=2, auto_decompress=False)

    @staticmethod
    async def cache_headers(request: web.Request, response: web.StreamResponse) -> None:
        response.headers.setdefault("Cache-Control", "no-store")

    async def start(self) -> str:
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        except BaseException:
            await self.runner.cleanup()
            raise
        port = self.runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def close(self) -> None:
        self.stopping = True
        self.daemon.changed.set()
        await self.runner.cleanup()

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

    def iter_tasks(self, session: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
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
                        yield collection, {"path": identity, "error": str(exc)}
                    continue
                if session is not None and item[session_field] != session:
                    continue
                destination = "linked_watches" if mode == "wait" and "owner" in item else collection
                yield destination, item

    def snapshot(self, session: str | None = None) -> dict[str, Any]:
        view = {"goals": [], "loops": [], "watches": [], "linked_watches": []}
        for collection, item in self.iter_tasks(session):
            view[collection].append(item)
        return view

    def sessions(self) -> list[dict[str, Any]]:
        sessions = {}
        kinds = {"goals": "goal", "loops": "loop", "watches": "wait", "linked_watches": "wait"}
        for collection, item in self.iter_tasks():
            kind = kinds[collection]
            identity = item.get("thread" if kind == "goal" else "session")
            if not identity:
                continue
            counts = sessions.setdefault(identity, {"id": identity, "wait": 0, "loop": 0, "goal": 0})
            counts[kind] += 1
        return [sessions[identity] for identity in sorted(sessions)]

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

    async def state_response(self, request: web.Request) -> web.Response:
        return web.json_response(self.snapshot(request_session(request)), dumps=dashboard_json)

    async def sessions_response(self, request: web.Request) -> web.Response:
        return web.json_response(self.sessions(), dumps=dashboard_json)

    async def action_response(self, request: web.Request) -> web.Response:
        if request.headers.get("Content-Type") != "application/json":
            raise ValueError("Expected application/json")
        if not 0 < (request.content_length or 0) <= MAX_ACTION_BYTES or "Content-Encoding" in request.headers:
            raise ValueError("Invalid body length or encoding")
        async with asyncio.timeout(10):
            body = await request.json()
        return web.json_response(await self.action(body, request_session(request)), dumps=dashboard_json)

    async def stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        session = request_session(request)
        await response.prepare(request)
        with suppress(OSError):
            while not self.stopping:
                changed = self.daemon.changed
                payload = dashboard_json(self.snapshot(session))
                await response.write(f"data: {payload}\n\n".encode())

                # Keep idle previews connected without rereading task state.
                while not changed.is_set() and not self.stopping:
                    try:
                        await asyncio.wait_for(changed.wait(), 20)
                    except TimeoutError:
                        await response.write(b": keepalive\n\n")
        return response
