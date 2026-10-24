import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ubermensch import config
from ubermensch.backups import stage_snapshot, verify_snapshot, create_archive, restore_archive, unpack_verified
from ubermensch.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.vault, self.documents = self.root / 'vault', self.root / 'documents'
        self.vault.mkdir()
        self.documents.mkdir()
        for name, value in [('DATA_DIR', self.data), ('VAULT_DIR', self.vault),
                            ('DOCUMENTS_DIR', self.documents), ('OUTPUT_DIR', self.vault / '08 AI/Reports')]:
            setting = patch.object(config, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.db_path = self.data / 'test.db'
        self.store = Store(self.db_path)
        self.addCleanup(self.store.db.close)
        self.store.add_task('Вернуться к музыке', '', None, None, 'test')
        for relative, text in {
            'agent/tasks.json': '[{"id":"task","status":"pending"}]',
            'agent/schedules.json': '[]', 'workspace/tg_style_work.md': 'style',
            'personalization/profile.json': '{}', 'raw/import/source.txt': 'source',
            'secrets/token': 'synthetic-secret', 'workspace/.env': 'synthetic-secret',
        }.items():
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        reports = self.vault / '08 AI/Reports'
        reports.mkdir(parents=True)
        (reports / 'Уроки Ани.md').write_text('lesson')
        (self.vault / 'Дневник.md').write_text('journal')
        (self.documents / 'report.txt').write_text('report')

    def test_manifest_contains_agent_diary_documents_and_excludes_credentials(self):
        snapshot = self.root / 'snapshot'
        manifest = stage_snapshot(self.db_path, snapshot)
        self.assertIn('data/agent/tasks.json', manifest['files'])
        self.assertIn('data/workspace/tg_style_work.md', manifest['files'])
        self.assertIn('vault/Дневник.md', manifest['files'])
        self.assertIn('documents/report.txt', manifest['files'])
        self.assertFalse((snapshot / 'data/secrets').exists())
        self.assertFalse((snapshot / 'data/workspace/.env').exists())
        self.assertEqual(verify_snapshot(snapshot)['files'], manifest['files'])

    def test_corruption_is_detected(self):
        snapshot = self.root / 'snapshot'
        stage_snapshot(self.db_path, snapshot)
        (snapshot / 'data/agent/tasks.json').write_text('corrupt')
        with self.assertRaises(ValueError):
            verify_snapshot(snapshot)

    def test_symlink_is_not_followed_into_secrets(self):
        (self.data / 'workspace' / 'linked').symlink_to(self.data / 'secrets', target_is_directory=True)
        manifest = stage_snapshot(self.db_path, self.root / 'snapshot')
        self.assertIn('data/workspace/linked', manifest['excluded'])
        self.assertFalse((self.root / 'snapshot/data/workspace/linked').exists())

    @unittest.skipUnless(shutil.which('7z') or shutil.which('7za'), '7-Zip unavailable')
    def test_encrypted_archive_restores_database_and_files_to_new_directory(self):
        archive = self.root / 'test.7z'
        create_archive(self.db_path, archive, 'synthetic-test-password')
        restored = self.root / 'restored'
        restore_archive(archive, restored, 'synthetic-test-password')
        database = Store(restored / 'data/db/ubermensch.db')
        try:
            self.assertEqual(database.tasks()[0]['title'], 'Вернуться к музыке')
        finally:
            database.db.close()
        self.assertEqual((restored / 'vault/Дневник.md').read_text(), 'journal')
        self.assertEqual((restored / 'data/agent/tasks.json').read_text(),
                         '[{"id":"task","status":"pending"}]')
        with self.assertRaises(FileExistsError):
            restore_archive(archive, restored, 'synthetic-test-password')
        with self.assertRaises(RuntimeError):
            unpack_verified(archive, self.root / 'wrong-password', 'incorrect')


if __name__ == '__main__':
    unittest.main()
