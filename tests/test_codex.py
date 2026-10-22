from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
import waitd


class CodexDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.socket = self.root / "codex.sock"
        self.status = "idle"
        self.calls = []
        self.starts = []
        self.headers = []
        self.drop_ack = False
        self.reject_start = False
        self.bad_handshake = False
        self.fragment = False
        self.connections = set()
        self.server = await asyncio.start_unix_server(self.serve, path=str(self.socket))

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.connections):
            writer.close()
            await writer.wait_closed()
        self.temp.cleanup()

    async def frame(self, writer, payload, opcode=1, final=True):
        header = bytes([(0x80 if final else 0) | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([length])
        elif length < 65536:
            header += bytes([126]) + struct.pack("!H", length)
        else:
            header += bytes([127]) + struct.pack("!Q", length)
        writer.write(header + payload)
        await writer.drain()

    async def respond(self, writer, message):
        payload = json.dumps(message).encode()
        if self.fragment:
            await self.frame(writer, payload[:8], final=False)
            await self.frame(writer, b"ping", opcode=9)
            await self.frame(writer, payload[8:], opcode=0)
        else:
            await self.frame(writer, payload)

    async def serve(self, reader, writer):
        self.connections.add(writer)
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            self.headers.append(head)
            key = next(line.split(":", 1)[1].strip() for line in head.splitlines() if line.startswith("Sec-WebSocket-Key:"))
            digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            accept = base64.b64encode(digest).decode()
            if self.bad_handshake:
                accept = "wrong"
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            while True:
                first, second = await reader.readexactly(2)
                length = second & 127
                if length == 126:
                    length = struct.unpack("!H", await reader.readexactly(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", await reader.readexactly(8))[0]
                self.assertTrue(second & 128, "client frames must be masked")
                mask = await reader.readexactly(4)
                data = await reader.readexactly(length)
                if first & 15 == 10:
                    continue
                request = json.loads(bytes(value ^ mask[i % 4] for i, value in enumerate(data)))
                method = request["method"]
                self.calls.append(request)
                if "id" not in request:
                    continue
                if method == "thread/read":
                    result = {"thread": {"id": "session", "status": {"type": self.status}}}
                elif method == "thread/resume":
                    result = {"thread": {"id": "session", "status": {"type": "idle"}}}
                elif method == "turn/start":
                    if self.reject_start:
                        await self.respond(writer, {"id": request["id"], "error": {"code": -32602, "message": "rejected"}})
                        continue
                    self.starts.append(request["params"])
                    if self.drop_ack:
                        return
                    result = {"turn": {"id": "turn-1", "status": "inProgress"}}
                else:
                    result = {}
                # Notifications can arrive before the matching RPC response.
                await self.respond(writer, {"method": "thread/status/changed", "params": {}})
                await self.respond(writer, {"id": request["id"], "result": result})
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.connections.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def invoke(self, *extra, remote=None, env=None):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SRC / "codex.py"),
            "--remote",
            remote or f"unix://{self.socket}",
            "--session",
            "session",
            "--timeout",
            "2",
            *extra,
            "--message",
            "$wait resume event-1 — Recheck state",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        return process.returncode, stdout.decode(), stderr.decode()

    async def test_starts_original_thread_without_desktop_or_queue_consumer(self):
        code, stdout, stderr = await self.invoke()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["turn_id"], "turn-1")
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(
            self.starts[0], {"threadId": "session", "input": [{"type": "text", "text": "$wait resume event-1 — Recheck state"}]}
        )
        self.assertNotIn("thread/resume", [call["method"] for call in self.calls])

    async def test_unloaded_thread_resumed_without_setting_overrides(self):
        self.status = "notLoaded"
        code, _, stderr = await self.invoke()
        self.assertEqual(code, 0, stderr)
        resume = next(call for call in self.calls if call["method"] == "thread/resume")
        self.assertEqual(resume["params"], {"threadId": "session", "excludeTurns": True})

    async def test_busy_thread_is_not_steered_or_started(self):
        self.status = "active"
        code, stdout, _ = await self.invoke()
        self.assertEqual(code, 75)
        self.assertEqual(json.loads(stdout)["status"], "busy")
        self.assertFalse(self.starts)

    async def test_lost_start_acknowledgement_is_unconfirmed(self):
        self.drop_ack = True
        code, _, stderr = await self.invoke()
        self.assertEqual(code, 71, stderr)
        self.assertIn("acknowledgement lost", stderr)
        self.assertEqual(len(self.starts), 1)

    async def test_explicit_rpc_rejection_is_not_success(self):
        self.reject_start = True
        code, _, stderr = await self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("turn/start rejected", stderr)
        self.assertFalse(self.starts)

    async def test_fragmented_messages_and_ping_are_supported(self):
        self.fragment = True
        code, _, stderr = await self.invoke()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.starts), 1)

    async def test_invalid_handshake_cannot_start_a_turn(self):
        self.bad_handshake = True
        code, _, stderr = await self.invoke()
        self.assertEqual(code, 70, stderr)
        self.assertFalse(self.starts)

    @asynccontextmanager
    async def tcp_endpoint(self) -> AsyncGenerator[str, None]:
        server = await asyncio.start_server(self.serve, host="127.0.0.1", port=0)
        try:
            port = server.sockets[0].getsockname()[1]
            yield f"ws://127.0.0.1:{port}"
        finally:
            server.close()
            await server.wait_closed()

    async def test_tcp_websocket_and_token_from_environment(self) -> None:
        async with self.tcp_endpoint() as remote:
            code, stdout, stderr = await self.invoke(
                "--remote-auth-token-env",
                "WAIT_TEST_TOKEN",
                remote=remote,
                env={**os.environ, "WAIT_TEST_TOKEN": "test-secret"},
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("Authorization: Bearer test-secret", self.headers[-1])
            self.assertNotIn("test-secret", stdout + stderr)

    async def test_waitd_runs_query_and_directly_starts_wake_turn(self) -> None:
        daemon = waitd.WaitDaemon(self.root / "registry.json")
        async with self.tcp_endpoint() as remote:
            submitted = daemon.submit(
                [
                    "--label",
                    "headless",
                    "--session",
                    "session",
                    "--event-note",
                    "Recheck the saved state",
                    "--remote",
                    remote,
                    "--",
                    sys.executable,
                    "-c",
                    "print('Ready')",
                ],
                str(self.root),
            )
            await asyncio.wait_for(daemon.tasks[submitted["watch_id"]], 5)
            result = daemon.watchers[submitted["watch_id"]]["result"]
            self.assertEqual(result["notification"], "started")
            self.assertEqual(result["stdout"], "Ready\n")
            self.assertIn(submitted["watch_id"][:8], self.starts[0]["input"][0]["text"])
