"""Shared subprocess execution and cancellation for waitd."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from contextlib import suppress

MAX_PROGRAM_OUTPUT_BYTES = 64 * 1024


async def wait_for_cancel(cancel_event: asyncio.Event, timeout: float) -> bool:
    """Return whether cancellation arrives before the timeout."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout)
    except TimeoutError:
        return False
    return True


async def run_program(
    command: list[str],
    timeout: float,
    cwd: str,
    on_started: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """Run once; preserve bounded output without interpreting it."""
    result: dict[str, object] = {"event": "exited", "exit_code": None}
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return {**result, "event": "start_failed", "error": str(exc), "stdout": "", "stderr": ""}

    async def drain(name: str, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(8192):
            remaining = MAX_PROGRAM_OUTPUT_BYTES - len(output[name])
            output[name].extend(chunk[:remaining])
            truncated[name] |= len(chunk) > remaining

    assert process.stdout is not None and process.stderr is not None
    readers = [
        asyncio.create_task(drain("stdout", process.stdout)),
        asyncio.create_task(drain("stderr", process.stderr)),
    ]
    try:
        if on_started:
            on_started(process.pid)
        await asyncio.wait_for(process.wait(), timeout)
        result["exit_code"] = process.returncode
    except TimeoutError:
        result["event"] = "timeout"
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        await asyncio.gather(*readers)
    result.update({name: data.decode("utf-8", errors="replace") for name, data in output.items()})
    result["output_truncated"] = any(truncated.values())
    return result
