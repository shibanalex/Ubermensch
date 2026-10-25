"""Копия архива на флешку: прерывается при остановке ядра, помещается по месту, понимает FAT."""
from __future__ import annotations

import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from ubermensch import maintenance

Usage = namedtuple("Usage", "total used free")


class ExternalCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.health = []
        self.maintenance = maintenance.Maintenance(self.root / "db.sqlite",
                                                   type("H", (), {"report": lambda s, k, m: self.health.append(m),
                                                                  "clear": lambda s, k, m: None})())

    def test_copy_stops_when_core_stops_and_leaves_no_partial(self):
        source = self.root / "anya-2026-10-25_14-00-00.7z"
        source.write_bytes(b"x" * (maintenance.CHUNK * 3))
        target = self.root / "copy.partial"
        self.maintenance._stop.set()
        with self.assertRaises(InterruptedError):
            self.maintenance._copy(source, target)

    def test_prune_makes_room_and_removes_broken_copies(self):
        folder = self.root / "AnyaBackup"
        folder.mkdir()
        (folder / "anya-2026-10-24_14-00-00.partial").write_bytes(b"x")
        for day in ("20", "21", "22", "23", "24"):
            (folder / f"anya-2026-10-{day}_10-00-00.7z").write_bytes(b"x" * 400)
        with patch.object(maintenance.shutil, "disk_usage", lambda p: Usage(2000, 0, 10_000)):
            self.assertTrue(maintenance.Maintenance._prune_external(folder, 400))
        left = sorted(p.name for p in folder.iterdir())
        # половина носителя = 1000 байт: под новый архив (400) остаётся один старый
        self.assertEqual(left, ["anya-2026-10-24_10-00-00.7z"])
        with patch.object(maintenance.shutil, "disk_usage", lambda p: Usage(2000, 0, 100)):
            self.assertFalse(maintenance.Maintenance._prune_external(folder, 400))

    def test_big_archive_is_not_copied_to_fat(self):
        local, external = self.root / "local", self.root / "AnyaBackup"
        local.mkdir()
        external.mkdir()
        (local / "anya-2026-10-25_14-00-00.7z").write_bytes(b"x")
        with patch.object(maintenance, "LOCAL_DIR", local), \
                patch.object(maintenance, "_external_folders", lambda: [external]), \
                patch.object(maintenance, "_filesystem", lambda p: "vfat"), \
                patch.object(maintenance, "FAT_LIMIT", 0), \
                patch.object(maintenance.config, "DATA_DIR", self.root):
            self.maintenance._copy_external()
        self.assertEqual(list(external.iterdir()), [])
        self.assertIn("FAT, до 4 ГБ", self.health[0])


if __name__ == "__main__":
    unittest.main()
