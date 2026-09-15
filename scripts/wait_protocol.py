"""Shared paths and compatibility identity for the wait service."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

RUNTIME_DIR = Path(f"/tmp/wait-skill-{os.getuid()}")
SOCKET_PATH = RUNTIME_DIR / "waitd.sock"
REGISTRY_PATH = RUNTIME_DIR / "registry.json"
PROTOCOL_VERSION = 1
STATE_COMMANDS = ("goal", "loop")
SERVICE_SOURCES = ("wait_protocol.py", "waitd.py", "wait_for.py", "wait_loop.py", "state_runner.py")


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in SERVICE_SOURCES:
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()[:16]


SERVICE_FINGERPRINT = source_fingerprint()
