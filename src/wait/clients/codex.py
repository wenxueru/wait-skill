#!/usr/bin/env python3
"""Start a wake turn on the owning Codex app-server without a Desktop client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Self
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect, unix_connect
from websockets.exceptions import WebSocketException

from .. import runtime

EXIT_UNAVAILABLE = runtime.EXIT_NOTIFY_FAILED
EXIT_UNCONFIRMED = runtime.EXIT_NOTIFY_UNCONFIRMED
EXIT_BUSY = 75
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class RpcError(ValueError):
    """An explicit app-server rejection; no successful start was acknowledged."""


class Unconfirmed(ValueError):
    """A turn may have started; automatically sending it again is unsafe."""


class AppServer:
    """Small bounded JSON-RPC client over Unix, WS, or TLS WebSockets."""

    def __init__(self, remote: str, timeout: float, token_env: str | None = None) -> None:
        self.deadline = time.monotonic() + timeout
        self.connection: ClientConnection
        self.request_id = 0
        self.remote = runtime.resolve_codex_remote(remote)
        self.token_env = token_env

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex delivery deadline exceeded")
        return remaining

    async def __aenter__(self) -> Self:
        headers = {}
        if self.token_env:
            token = os.environ.get(self.token_env, "")
            if not token or any(character in token for character in "\r\n"):
                raise ValueError("Codex bearer-token environment variable is missing or invalid")
            headers["Authorization"] = f"Bearer {token}"
        options = {
            "additional_headers": headers,
            "compression": None,
            "proxy": None,
            "open_timeout": self.remaining(),
            "ping_interval": None,
            "close_timeout": 0,
            "max_size": MAX_MESSAGE_BYTES,
        }
        if self.remote.startswith("unix://"):
            self.connection = await unix_connect(self.remote.removeprefix("unix://"), **options)
        else:
            url = urlsplit(self.remote)
            if not url.hostname or url.username or url.password or url.fragment or url.query:
                raise ValueError("invalid Codex endpoint; use an environment variable for authentication")
            self.connection = await connect(self.remote, **options)
        try:
            await self.call("initialize", {"clientInfo": {"name": "wait_skill", "title": "Wait", "version": "2.0.0"}})
            await self.send({"method": "initialized", "params": {}})
        except BaseException:
            await self.connection.close()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.connection.close()

    async def send(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False).encode()
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("oversized WebSocket message")
        async with asyncio.timeout(self.remaining()):
            await self.connection.send(payload, text=True)

    async def receive(self) -> dict:
        async with asyncio.timeout(self.remaining()):
            payload = await self.connection.recv()
        if not isinstance(payload, str):
            raise TypeError("expected a JSON WebSocket text message")
        message = json.loads(payload)
        if not isinstance(message, dict):
            raise TypeError("invalid app-server JSON-RPC message")
        return message

    async def call(self, method: str, params: dict) -> dict:
        self.request_id += 1
        request_id = self.request_id
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = await self.receive()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RpcError(f"{method} rejected: {message['error']}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise ValueError("invalid app-server RPC response")
                return result
            if "id" in message and "method" in message:
                # Delivery never answers approval, tool, or user-input requests.
                raise OSError("app-server requested client interaction during wake delivery")


async def wake(remote: str, session: str, message: str, timeout: float, token_env: str | None = None) -> tuple[dict, int]:
    async with AppServer(remote, timeout, token_env) as server:
        thread = (await server.call("thread/read", {"threadId": session}))["thread"]
        if not isinstance(thread, dict) or thread.get("id") != session:
            raise ValueError("app-server returned a different thread")
        status = thread["status"]["type"]
        if status == "notLoaded":
            thread = (await server.call("thread/resume", {"threadId": session, "excludeTurns": True}))["thread"]
            if not isinstance(thread, dict) or thread.get("id") != session:
                raise ValueError("app-server resumed a different thread")
            status = thread["status"]["type"]
        if status == "active":
            return {"status": "busy", "thread_id": session}, EXIT_BUSY
        if status != "idle":
            raise RpcError(f"cannot wake thread in status {status!r}")
        # Never override model, working directory, sandbox, approval policy, or instructions.
        # Once this request may have been sent, transport failures are ambiguous.
        try:
            result = await server.call("turn/start", {"threadId": session, "input": [{"type": "text", "text": message}]})
            turn = result["turn"]
            if (
                not isinstance(turn, dict)
                or not isinstance(turn.get("id"), str)
                or not turn["id"]
                or turn.get("status") not in {"inProgress", "completed"}
            ):
                raise ValueError("turn/start did not confirm a running turn")
        except RpcError:
            raise
        except (OSError, WebSocketException, ValueError, KeyError, TypeError) as exc:
            raise Unconfirmed("Codex turn/start acknowledgement lost; inspect the original thread before retrying") from exc
        return {"status": "started", "thread_id": session, "turn_id": turn["id"]}, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--remote-auth-token-env")
    parser.add_argument("--timeout", type=runtime.positive_number, default=50.0)
    parser.add_argument("--message", required=True)
    args = parser.parse_args(argv)
    try:
        result, code = asyncio.run(wake(args.remote, args.session, args.message, args.timeout, args.remote_auth_token_env))
    except Unconfirmed as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNCONFIRMED
    except (OSError, WebSocketException):
        # Do not echo server responses or token-bearing transport details.
        print("notification_unavailable: cannot connect to Codex app-server", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except (ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
