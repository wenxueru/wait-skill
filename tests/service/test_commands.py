from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from wait.service.commands import StateCommandRunner


class StateCommandRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_includes_lock_wait(self):
        runner = StateCommandRunner()
        async with runner.locks["goal"]:
            with patch("wait.service.commands.STATE_COMMAND_TIMEOUT", 0.01):
                result = await asyncio.wait_for(runner.run("goal", ["check"], Path.cwd()), timeout=0.1)
        self.assertEqual(result["code"], 124)
