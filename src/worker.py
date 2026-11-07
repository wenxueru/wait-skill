"""Run internal wait workers from any working directory."""

from __future__ import annotations

import argparse
from importlib import import_module


def main() -> int:
    commands = {
        "daemon": "wait.service.daemon",
        "goal": "wait.tasks.goal",
        "loop": "wait.tasks.loop",
        "codex": "wait.clients.codex",
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=commands)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return import_module(commands[args.mode]).main(args.argv)


if __name__ == "__main__":
    raise SystemExit(main())
