"""Run durable state engines for the local wait service."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

from wait_protocol import STATE_COMMANDS

STATE_SCRIPTS = {name: Path(__file__).with_name(f"wait_{name}.py") for name in STATE_COMMANDS}
STATE_COMMAND_TIMEOUT = 30.0


class StateCommandRunner:
    """Run goal and loop state engines with independent serialization."""

    def __init__(self) -> None:
        self.locks = {name: asyncio.Lock() for name in STATE_SCRIPTS}

    async def run(self, name: str, argv: list[str], cwd: Path) -> dict[str, object]:
        async def execute() -> tuple[bytes, bytes, int]:
            async with self.locks[name]:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    os.fspath(STATE_SCRIPTS[name]),
                    *argv,
                    cwd=cwd,
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
        except asyncio.TimeoutError:
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
