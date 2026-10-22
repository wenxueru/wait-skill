#!/usr/bin/env python3
"""Start a wake turn on the owning Codex app-server without a Desktop client."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import struct
import sys
import time
from contextlib import suppress
from typing import Self
from urllib.parse import urlsplit

import runtime

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
        self.connection: socket.socket | None = None
        self.buffer = bytearray()
        self.request_id = 0
        self.remote = runtime.resolve_codex_remote(remote)
        self.token_env = token_env

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex delivery deadline exceeded")
        return remaining

    def __enter__(self) -> Self:
        try:
            host, target = self._connect()
            self._upgrade(host, target)
            self.call(
                "initialize",
                {
                    "clientInfo": {"name": "wait_skill", "title": "Wait", "version": "2.0.0"},
                },
            )
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.close()
            raise

    def _connect(self) -> tuple[str, str]:
        if self.remote.startswith("unix://"):
            self.connection = socket.socket(socket.AF_UNIX)
            self.connection.settimeout(self.remaining())
            self.connection.connect(self.remote.removeprefix("unix://"))
            return "localhost", "/"

        url = urlsplit(self.remote)
        if not url.hostname or url.username or url.password or url.fragment or url.query:
            raise ValueError("invalid Codex endpoint; use an environment variable for authentication")
        port = url.port or (443 if url.scheme == "wss" else 80)
        self.connection = socket.create_connection((url.hostname, port), timeout=self.remaining())
        if url.scheme == "wss":
            self.connection.settimeout(self.remaining())
            self.connection = ssl.create_default_context().wrap_socket(
                self.connection,
                server_hostname=url.hostname,
            )
        return url.netloc, url.path or "/"

    def _upgrade(self, host: str, target: str) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        headers = [
            f"GET {target} HTTP/1.1",
            f"Host: {host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.token_env:
            token = os.environ.get(self.token_env, "")
            if not token or any(c in token for c in "\r\n"):
                raise ValueError("Codex bearer-token environment variable is missing or invalid")
            headers.append(f"Authorization: Bearer {token}")
        self.send_bytes(("\r\n".join(headers) + "\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            if len(self.buffer) >= 16384:
                raise OSError("oversized WebSocket handshake")
            self.receive_bytes()
        head, rest = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(rest)
        lines = head.decode("latin-1").split("\r\n")
        response_headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                response_headers[name.lower()] = value.strip()
        digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        accept = base64.b64encode(digest).decode()
        if (
            lines[0].split()[1:2] != ["101"]
            or response_headers.get("sec-websocket-accept", "") != accept
            or response_headers.get("upgrade", "").lower() != "websocket"
            or "upgrade" not in response_headers.get("connection", "").lower()
        ):
            raise OSError("incompatible or rejected Codex WebSocket handshake")

    def close(self) -> None:
        if self.connection is not None:
            with suppress(OSError):
                self.connection.close()
            self.connection = None

    def __exit__(self, *_: object) -> None:
        self.close()

    def send_bytes(self, data: bytes) -> None:
        assert self.connection is not None
        self.connection.settimeout(self.remaining())
        self.connection.sendall(data)

    def receive_bytes(self) -> None:
        assert self.connection is not None
        self.connection.settimeout(self.remaining())
        data = self.connection.recv(8192)
        if not data:
            raise ConnectionError("Codex app-server disconnected")
        self.buffer.extend(data)

    def take(self, size: int) -> bytes:
        while len(self.buffer) < size:
            self.receive_bytes()
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def frame(self, opcode: int, payload: bytes) -> None:
        size = len(payload)
        if size > MAX_MESSAGE_BYTES:
            raise ValueError("oversized WebSocket message")
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size <= 65535:
            header += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", size)
        mask = secrets.token_bytes(4)
        self.send_bytes(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, message: dict) -> None:
        self.frame(1, json.dumps(message, ensure_ascii=False).encode())

    def receive(self) -> dict:
        payload = bytearray()
        fragmented = False
        while True:
            first, second = self.take(2)
            final, opcode = bool(first & 0x80), first & 0x0F
            if first & 0x70 or second & 0x80:
                raise OSError("invalid WebSocket frame flags")
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self.take(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.take(8))[0]
            if len(payload) + size > MAX_MESSAGE_BYTES:
                raise OSError("oversized WebSocket message")
            chunk = self.take(size)
            if opcode >= 8:
                if not final or size > 125:
                    raise OSError("invalid WebSocket control frame")
                if opcode == 8:
                    raise ConnectionError("Codex app-server closed WebSocket")
                if opcode == 9:
                    self.frame(10, chunk)
                elif opcode != 10:
                    raise OSError("unsupported WebSocket control frame")
                continue
            if opcode != (0 if fragmented else 1):
                raise OSError("expected a JSON WebSocket text message")
            payload.extend(chunk)
            if final:
                message = json.loads(payload)
                if not isinstance(message, dict):
                    raise ValueError("invalid app-server JSON-RPC message")
                return message
            fragmented = True

    def call(self, method: str, params: dict) -> dict:
        self.request_id += 1
        request_id = self.request_id
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
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


def wake(remote: str, session: str, message: str, timeout: float, token_env: str | None = None) -> tuple[dict, int]:
    with AppServer(remote, timeout, token_env) as server:
        thread = server.call("thread/read", {"threadId": session})["thread"]
        if not isinstance(thread, dict) or thread.get("id") != session:
            raise ValueError("app-server returned a different thread")
        status = thread["status"]["type"]
        if status == "notLoaded":
            thread = server.call("thread/resume", {"threadId": session, "excludeTurns": True})["thread"]
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
            result = server.call("turn/start", {"threadId": session, "input": [{"type": "text", "text": message}]})
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
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Unconfirmed("Codex turn/start acknowledgement lost; inspect the original thread before retrying") from exc
        return {"status": "started", "thread_id": session, "turn_id": turn["id"]}, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--remote-auth-token-env")
    parser.add_argument("--timeout", type=runtime.positive_number, default=50.0)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    try:
        result, code = wake(args.remote, args.session, args.message, args.timeout, args.remote_auth_token_env)
    except Unconfirmed as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNCONFIRMED
    except OSError:
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
