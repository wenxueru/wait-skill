from __future__ import annotations

import asyncio
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
import goal
import loop
import runtime
import waitctl
import waitd


class PollServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_poll_program_returns_through_native_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "custom_wait.py"
            program.write_text(
                "import sys, json\n"
                f"sys.path.insert(0, {str(SRC)!r})\n"
                "from polling import poll\n"
                "values = iter([{'healthy': 0}, {'healthy': 8}])\n"
                "def query(): return next(values)\n"
                "def evaluate(data):\n"
                "    return {'workers': data['healthy']} if data['healthy'] >= 8 else None\n"
                "print(json.dumps(poll(query, evaluate, interval=.01)))\n",
                encoding="utf-8",
            )
            daemon = waitd.WaitDaemon(root / "registry.json")
            submitted = daemon.submit(
                [
                    "--label",
                    "custom",
                    "--event-note",
                    "Recheck the custom state",
                    "--client",
                    "claude",
                    "--session",
                    "test",
                    "--",
                    sys.executable,
                    str(program),
                ],
                str(root),
            )
            task = daemon.tasks[submitted["watch_id"]]
            try:
                response = await asyncio.wait_for(daemon.follow(submitted["watch_id"]), 3)
                await task
                self.assertEqual(response["result"]["event"], "exited")
                self.assertEqual(response["result"]["exit_code"], 0)
                self.assertEqual(json.loads(response["result"]["stdout"]), {"workers": 8})
                self.assertIn(submitted["watch_id"][:8], response["resume_message"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class DocumentationTest(unittest.TestCase):
    def setUp(self) -> None:
        root = SRC.parent
        self.files = [*root.glob("README*.md"), *root.glob("SKILL*.md"), *root.joinpath("docs").glob("*.md")]

    def test_local_links_and_section_anchors_resolve(self) -> None:
        for source in self.files:
            for link in re.findall(r"\]\(([^)]+)\)", source.read_text()):
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
            for block in re.findall(r"```bash\n(.*?)```", source.read_text(), flags=re.DOTALL):
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


if __name__ == "__main__":
    unittest.main()
