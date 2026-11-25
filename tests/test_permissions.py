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

    async def test_host_shell_requires_approval_for_arbitrary_programs(self):
        with patch('ubermensch.capabilities.computer._ask', AsyncMock(return_value=False)) as ask, \
                patch.object(shell, 'run') as execute:
            result = await run_shell('python3 -c "print(1)"', 'test', mode='host')
            self.assertIn('не разрешил', result)
            ask.assert_awaited_once()
            execute.assert_not_called()

    async def test_desktop_control_runs_on_host_without_approval(self):
        with patch('ubermensch.capabilities.computer._ask', AsyncMock()) as ask, \
                patch.object(shell, 'run_async', AsyncMock(return_value='ok')) as execute:
            await run_shell('wpctl set-volume @DEFAULT_AUDIO_SINK@ 40%', 'громкость', mode='host')
            ask.assert_not_awaited()
            execute.assert_awaited_once_with('wpctl set-volume @DEFAULT_AUDIO_SINK@ 40%')

    def test_only_plain_safe_commands_skip_approval(self):
        free = ['kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled true',
                'qdbus6 org.kde.KWin /KWin reconfigure', 'playerctl play-pause',
                'ps aux | grep -i musescore | head -5', 'systemctl --user status plasma-plasmashell',
                'journalctl --user -n 50', 'brightnessctl set 60%', 'df -h && free -h']
        guarded = ['awk \'BEGIN{system("rm -rf ~")}\'', 'sed -n "1e rm x" f', 'kstart rm -rf ~',
                   'ls > out.txt', 'cat $(echo /etc/passwd)', 'echo `id`', 'ls & rm -rf ~',
                   'find ~ -name x -delete', 'find ~ -exec rm {} +', 'cat ~/.ssh/id_ed25519',
                   'cat ~/Projects/HHruBot/.env', 'ls ~/Projects/Ubermensch/secrets',
                   'systemctl --user stop ubermensch', 'loginctl terminate-session 3',
                   'qdbus6 org.kde.ksmserver /KSMServer logout 0 0 0', 'journalctl --vacuum-size=1M',
                   'sudo ls', 'ls; rm -rf ~', 'grep -r token ~/.config', 'cat ~/.config/google-chrome/Default/Cookies',
                   'cat a 2>/tmp/log', 'find ~ -name a 2>/dev/null -delete']
        free.append('find /home/allegro -iname "*тренир*" 2>/dev/null | head')  # verify find pipeline stays within read allowlist
        for command in free:
            self.assertTrue(shell.runs_on_host_freely(command), command)
        for command in guarded:
            self.assertFalse(shell.runs_on_host_freely(command), command)

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
