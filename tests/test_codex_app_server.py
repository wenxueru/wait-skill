from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from codex import AppServer, wake

requests = []


class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        message = {
            "id": "msg-smoke",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Headless wake verified", "annotations": []}],
        }
        response = {
            "id": "resp-smoke",
            "object": "response",
            "status": "completed",
            "output": [message],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            {"type": "response.created", "response": {"id": "resp-smoke", "status": "in_progress", "output": []}},
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**message, "status": "in_progress", "content": []},
            },
            {"type": "response.output_item.done", "output_index": 0, "item": message},
            {"type": "response.completed", "response": response},
        ]
        payload = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def running_codex(root: Path, endpoint: str, model_port: int) -> Generator[None, None, None]:
    env = {**os.environ, "CODEX_HOME": str(root / "home")}
    env.pop("OPENAI_API_KEY", None)
    args = [
        "codex",
        "app-server",
        "--listen",
        endpoint,
        "-c",
        'model_provider="wait_smoke"',
        "-c",
        'model="wait-smoke"',
        "-c",
        'model_providers.wait_smoke.name="Wait isolated stub"',
        "-c",
        f'model_providers.wait_smoke.base_url="http://127.0.0.1:{model_port}/v1"',
        "-c",
        'model_providers.wait_smoke.wire_api="responses"',
        "-c",
        "model_providers.wait_smoke.requires_openai_auth=false",
        "-c",
        "model_providers.wait_smoke.supports_websockets=false",
    ]
    with (root / "server.log").open("w+") as log:
        server = subprocess.Popen(
            args,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if (root / "server.sock").exists():
                    break
                if server.poll() is not None:
                    raise RuntimeError("server exited")
                time.sleep(0.05)
            yield
        except BaseException:
            log.flush()
            log.seek(0)
            print(log.read()[-6000:], file=sys.stderr)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


@unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
class RealAppServerTest(unittest.TestCase):
    def test_turn_continues_after_all_clients_disconnect(self) -> None:
        requests.clear()
        stub = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="wait-real-codex-") as temp:
                root = Path(temp)
                (root / "home").mkdir()
                endpoint = f"unix://{root / 'server.sock'}"
                with running_codex(root, endpoint, stub.server_port):
                    with AppServer(endpoint, 10) as rpc:
                        thread = rpc.call(
                            "thread/start",
                            {
                                "cwd": temp,
                                "model": "wait-smoke",
                                "approvalPolicy": "never",
                                "sandbox": "read-only",
                            },
                        )["thread"]
                    # No client remains attached; wake independently, then disconnect as well.
                    result, code = wake(endpoint, thread["id"], "Verify the background continuation.", 10)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result["status"], "started", result)
                    for _ in range(100):
                        with AppServer(endpoint, 5) as rpc:
                            read = rpc.call(
                                "thread/read",
                                {
                                    "threadId": thread["id"],
                                    "includeTurns": False,
                                },
                            )["thread"]
                        if requests and read["status"]["type"] == "idle":
                            break
                        time.sleep(0.05)
                    self.assertEqual(read["status"]["type"], "idle", read)
                    self.assertTrue(requests, "model backend was never called")
                    self.assertTrue(
                        any(
                            b"Headless wake verified" in file.read_bytes()
                            for file in (root / "home").rglob("*")
                            if file.is_file()
                        ),
                        "completion not persisted",
                    )
        finally:
            stub.shutdown()
            stub.server_close()
