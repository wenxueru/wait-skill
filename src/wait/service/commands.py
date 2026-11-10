"""Serialize Goal/Loop state commands independently of watcher supervision."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

from .. import runtime
from ..protocol import SOCKET_PATH, STATE_COMMANDS

STATE_COMMAND_TIMEOUT = 30.0


class StateCommandRunner:
    """Run goal and loop state engines with independent serialization."""

    def __init__(self, socket_path: Path = SOCKET_PATH) -> None:
        # State commands reach back through this socket, e.g. to submit a watcher.
        self.socket_path = socket_path
        self.locks = {name: asyncio.Lock() for name in STATE_COMMANDS}

    async def run(self, name: str, argv: list[str], cwd: Path) -> dict[str, object]:
        async def execute() -> tuple[bytes, bytes, int]:
            async with self.locks[name]:
                process = await asyncio.create_subprocess_exec(
                    *runtime.worker_command(name, *argv),
                    cwd=cwd,
                    env={**os.environ, "WAITD_SOCKET": str(self.socket_path)},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                communication = asyncio.create_task(process.communicate())
                try:
                    stdout, stderr = await asyncio.shield(communication)
                except asyncio.CancelledError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await communication
                    raise
                assert process.returncode is not None
                return stdout, stderr, process.returncode

        try:
            stdout, stderr, returncode = await asyncio.wait_for(execute(), STATE_COMMAND_TIMEOUT)
        except TimeoutError:
            return {
                "code": 124,
                "output": "",
                "error": f"{name} command exceeded {STATE_COMMAND_TIMEOUT:g} seconds",
            }
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()
        try:
            parsed: object = json.loads(output)
        except json.JSONDecodeError:
            parsed = output
        return {"code": returncode, "output": parsed, "error": error}
