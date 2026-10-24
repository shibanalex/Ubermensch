import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ubermensch import config
from ubermensch.capabilities.computer import run_shell
from ubermensch.system import shell


class ShellPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_timeout_kills_its_process_group(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            with self.assertRaises(TimeoutError):
                await shell.run_async('sleep 10', timeout=.02)

    async def test_host_shell_always_requires_approval_even_if_disguised(self):
        with patch('ubermensch.capabilities.computer._ask', AsyncMock(return_value=False)) as ask, \
                patch.object(shell, 'run') as execute:
            result = await run_shell('python3 -c "print(1)"', 'test', mode='host')
            self.assertIn('не разрешил', result)
            ask.assert_awaited_once()
            execute.assert_not_called()

    async def test_disabled_shell_does_not_execute(self):
        with patch.dict(os.environ, {'UBERMENSCH_SHELL_MODE': 'disabled'}), patch.object(shell, 'run') as execute:
            self.assertIn('отключён', await run_shell('echo test', 'test'))
            execute.assert_not_called()

    def test_child_does_not_inherit_credentials_or_startup_hooks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)), \
                patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-key', 'BASH_ENV': '/test'}), \
                patch.object(shell.subprocess, 'run', return_value=SimpleNamespace(
                    stdout=b'ok', stderr=b'', returncode=0)) as execute:
            shell.run('echo test')
            environment = execute.call_args.kwargs['env']
            self.assertNotIn('OPENAI_API_KEY', environment)
            self.assertNotIn('BASH_ENV', environment)

    def test_missing_sandbox_does_not_fall_back_to_host(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)), \
                patch.object(shell.shutil, 'which', return_value=None), patch.object(shell.subprocess, 'run') as execute:
            with self.assertRaises(RuntimeError):
                shell.run('echo test', sandbox=True)
            execute.assert_not_called()
