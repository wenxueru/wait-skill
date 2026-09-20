from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
import waitd  # noqa: E402


class PollServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_poll_program_returns_through_native_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "custom_wait.py"
            program.write_text(
                "import sys, json\n"
                f"sys.path.insert(0, {str(SRC)!r})\n"
                "from wait_for import poll\n"
                "values = iter([{'healthy': 0}, {'healthy': 8}])\n"
                "def query(): return next(values)\n"
                "def evaluate(data):\n"
                "    return {'workers': data['healthy']} if data['healthy'] >= 8 else None\n"
                "print(json.dumps(poll(query, evaluate, interval=.01)))\n",
                encoding="utf-8",
            )
            daemon = waitd.WaitDaemon(root / "registry.json")
            submitted = daemon.submit([
                "--label", "custom", "--event-note", "Recheck the custom state",
                "--client", "claude", "--session", "test",
                "--", sys.executable, str(program),
            ], str(root))
            task = daemon.tasks[submitted["watch_id"]]
            try:
                response = await asyncio.wait_for(daemon.follow(submitted["watch_id"]), 3)
                await task
                self.assertEqual(response["result"]["event"], "exited")
                self.assertEqual(response["result"]["exit_code"], 0)
                self.assertEqual(json.loads(response["result"]["stdout"]), {"workers": 8})
                self.assertIn(submitted["log_file"], response["resume_message"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
