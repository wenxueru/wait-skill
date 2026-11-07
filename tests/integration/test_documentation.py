from __future__ import annotations

import re
import shlex
import unittest

import waitctl
from tests import ROOT
from wait import runtime
from wait.tasks import goal, loop


class DocumentationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.files = [*ROOT.glob("README*.md"), *ROOT.glob("SKILL*.md"), *ROOT.joinpath("docs").glob("*.md")]

    def test_local_links_and_section_anchors_resolve(self) -> None:
        for source in self.files:
            prose = re.sub(r"```.*?```|`[^`\n]*`", "", source.read_text(), flags=re.DOTALL)
            for link in re.findall(r"\]\(([^)]+)\)", prose):
                if "://" in link:
                    continue
                path, _, anchor = link.partition("#")
                target = source.parent / path if path else source
                with self.subTest(source=source.name, link=link):
                    self.assertTrue(target.is_file(), f"Missing linked file: {target}")
                    if anchor:
                        prose = re.sub(r"```.*?```", "", target.read_text(), flags=re.DOTALL)
                        headings = re.findall(r"^#+\s+(.+)$", prose, flags=re.MULTILINE)
                        slugs = [re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-") for heading in headings]
                        self.assertIn(anchor, slugs)

    def test_shell_examples_match_the_current_command_parsers(self) -> None:
        checked = 0
        for source in self.files:
            for block in re.findall(r"```(?:bash|sh)\n(.*?)```", source.read_text(), flags=re.DOTALL):
                for line in block.replace("\\\n", " ").splitlines():
                    argv = shlex.split(line, comments=True)
                    if len(argv) < 2 or not argv[1].endswith("/src/waitctl.py"):
                        continue
                    with self.subTest(source=source.name, command=line):
                        args = waitctl.parser().parse_args(argv[2:])
                        if args.command == "start":
                            runtime.parse_job_args(waitctl.forwarded_args(args.argv))
                        elif args.command in {"goal", "loop"}:
                            parser = goal.parser() if args.command == "goal" else loop.parser()
                            parser.parse_args(waitctl.forwarded_args(args.argv))
                        checked += 1
        self.assertGreater(checked, 0)
