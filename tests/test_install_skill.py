from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from install_skill import PACKAGED, install
from tests import ROOT


class InstallTest(unittest.TestCase):
    def test_replaces_old_skills_and_removes_matching_aliases_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "source", root / "skills" / "wait"
            source.mkdir()
            for name in PACKAGED:
                item = source / name
                if name in {"src", "docs", "agents", "assets"}:
                    item.mkdir()
                    (item / "new").touch()
                else:
                    item.write_text("name: wait\n")
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_text("name: wait\n")
            (destination / "src").mkdir()
            (destination / "src" / "obsolete").touch()
            old = destination / "wait-goal"
            old.mkdir()
            (old / "SKILL.md").write_text("name: wait-goal\n")
            alias = destination.parent / "wait-goal"
            alias.symlink_to(old)
            unrelated = destination.parent / "wait-loop"
            unrelated.mkdir()
            (unrelated / "keep").touch()
            install(source, destination)
            self.assertFalse(alias.is_symlink())
            self.assertFalse(old.exists())
            self.assertFalse((destination / "src" / "obsolete").exists())
            self.assertTrue((destination / "src" / "new").exists())
            self.assertTrue((unrelated / "keep").exists())
            self.assertEqual(list(destination.rglob("SKILL.md")), [destination / "SKILL.md"])

    def test_rejects_unrelated_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "not the wait skill"):
                install(ROOT, root)

    def test_installed_entry_points_work_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "installed"
            workspace = root / "workspace"
            workspace.mkdir()
            install(ROOT, destination)
            commands = [
                ("waitctl.py", "--help"),
                ("worker.py", "daemon", "--help"),
                ("worker.py", "codex", "--help"),
                (
                    "worker.py",
                    "goal",
                    "init",
                    "--objective",
                    "Installed goal",
                    "--session",
                    "test",
                    "--state",
                    str(root / "goal.json"),
                ),
                (
                    "worker.py",
                    "loop",
                    "init",
                    "--task",
                    "Installed loop",
                    "--event-note",
                    "Check again",
                    "--session",
                    "test",
                    "--interval",
                    "60",
                    "--no-notify",
                    "--state",
                    str(root / "loop.json"),
                ),
            ]
            for script, *arguments in commands:
                with self.subTest(script=script, arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, str(destination / "src" / script), *arguments],
                        check=False,
                        cwd=workspace,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / "goal.json").read_text())["objective"], "Installed goal")
            self.assertEqual(json.loads((root / "loop.json").read_text())["task"], "Installed loop")
