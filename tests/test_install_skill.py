from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from install_skill import PACKAGED, install


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
                install(Path(__file__).parents[1], root)
