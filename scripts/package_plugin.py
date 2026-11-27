"""Build the Wait Workflow ZIP from this repository's packaged skill resources."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from src.install_skill import PACKAGED


def package_source(root: Path, name: str) -> Path:
    source = root / name
    if not source.is_file() or source.resolve() != source:
        raise ValueError(f"Package input must be a regular file in the checkout: {name}")
    return source


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = package_source(root, "plugin.json")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    filename = f"{metadata['name']}-{metadata['version']}.zip"
    output = root / "dist" / filename
    tracked = subprocess.check_output(["git", "ls-files", "--deduplicate", "-z", "--", *PACKAGED], cwd=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        draft = Path(temporary) / filename
        with ZipFile(draft, "w", compression=ZIP_DEFLATED) as package:
            package.write(manifest, "plugin.json")
            for name in tracked.decode().rstrip("\0").split("\0"):
                package.write(package_source(root, name), f"skills/wait/{name}")
        draft.replace(output)
    print(output)


if __name__ == "__main__":
    main()
