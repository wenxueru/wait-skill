"""Install the unified wait skill and remove its obsolete skill registrations."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGED = (
    "SKILL.md",
    "SKILL.zh-CN.md",
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "src",
    "docs",
    "agents",
    "assets",
)


def install(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.expanduser().absolute()
    if destination != source and (destination.is_relative_to(source) or source.is_relative_to(destination)):
        raise ValueError("source and destination must be separate directories")
    if destination.is_symlink():
        raise ValueError("destination must be a directory, not a symbolic link")
    same_directory = destination.resolve() == source
    if destination.exists() and not same_directory:
        skill = destination / "SKILL.md"
        if not skill.is_file() or "name: wait\n" not in skill.read_text():
            raise ValueError("existing destination is not the wait skill")
    # Remove only registrations that resolve to the old nested skills.
    for mode in ("goal", "loop"):
        alias = destination.parent / f"wait-{mode}"
        nested = destination / f"wait-{mode}"
        if alias.is_symlink() and alias.resolve() == nested.resolve():
            alias.unlink()
        if nested.is_symlink():
            nested.unlink()
        elif nested.exists():
            skill = nested / "SKILL.md"
            if not skill.is_file() or f"name: wait-{mode}\n" not in skill.read_text():
                raise ValueError(f"unrecognized nested directory: {nested}")
            shutil.rmtree(nested)
    if same_directory:
        return
    destination.mkdir(parents=True, exist_ok=True)
    for name in PACKAGED:
        item, target = source / name, destination / name
        if target.is_symlink():
            target.unlink()
        if item.is_dir():
            # These are packaged resources; replace them directly without backups.
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_deps"))
        else:
            shutil.copy2(item, target)


def install_with_dependencies(source: Path, destination: Path) -> None:
    if sys.version_info < (3, 11):  # noqa: UP036 - the installer may run before Python is upgraded
        raise SystemExit("Wait requires Python 3.11 or newer. Run the installer with that interpreter.")
    with tempfile.TemporaryDirectory(prefix="wait-dependencies-") as temporary:
        dependencies = Path(temporary) / "_deps"
        args = ["--target", str(dependencies), "-r", str(source / "src" / "requirements.txt")]
        subprocess.run(
            [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", "--no-compile", *args], check=True
        )
        install(source, destination)
        target = destination.expanduser() / "src" / "_deps"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(dependencies, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser.add_argument("--destination", type=Path, default=root / "skills" / "wait")
    args = parser.parse_args()
    install_with_dependencies(Path(__file__).resolve().parents[1], args.destination)
    print(f"Installed one wait skill at {args.destination.expanduser().absolute()}")


if __name__ == "__main__":
    main()
