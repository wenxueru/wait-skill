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


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    sources = [package.parent / "worker.py", *sorted(package.rglob("*.py"))]
    for path in sources:
        digest.update(path.relative_to(package.parent).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


SERVICE_FINGERPRINT = source_fingerprint()
