import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QLockFile

from detect_app.__main__ import main
from detect_app.config import AppPaths
from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = AppPaths(root, root / "database.sqlite3", root, root, root, root, root, root)
        self.sessions = create_session_factory(self.paths.database)
        self.repository = AnalysisRepository(self.sessions)
        self.repository.create_job("pending", "original.png", "manual", "model", "a" * 64)
        self.lock_path = str(root / "nuts_vision.lock")

    def tearDown(self):
        self.sessions.kw["bind"].dispose()
        self.temporary.cleanup()

    def test_second_instance_does_not_touch_database_even_with_old_lock(self):
        lock = QLockFile(self.lock_path)
        lock.setStaleLockTime(0)
        self.assertTrue(lock.tryLock(0))
        os.utime(self.lock_path, (1, 1))
        original_database = self.paths.database.read_bytes()
        try:
            with (
                patch("detect_app.__main__.AppPaths.create", return_value=self.paths),
                patch("detect_app.__main__.QApplication"),
                patch("detect_app.__main__.QMessageBox.warning") as warning,
                patch("detect_app.__main__.create_session_factory") as create_database,
                patch("detect_app.__main__.MainWindow") as window,
            ):
                self.assertEqual(main(), 1)
                self.assertIn("déjà ouvert", warning.call_args.args[2])
                create_database.assert_not_called()
                window.assert_not_called()
            self.assertEqual(self.paths.database.read_bytes(), original_database)
            self.assertEqual(self.repository.get_job("pending")[0]["status"], "processing")
        finally:
            lock.unlock()

    def test_crashed_instance_lock_is_recovered_before_history_is_created(self):
        child = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys; from PySide6.QtCore import QLockFile; "
                "lock = QLockFile(sys.argv[1]); lock.setStaleLockTime(0); "
                "print(lock.tryLock(0), flush=True); sys.stdin.read()",
                self.lock_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "True")
        finally:
            child.kill()
            child.communicate(timeout=5)
        self.assertTrue(Path(self.lock_path).exists())

        def create_window(*args):
            log, _ = self.repository.get_job("pending")
            self.assertEqual(log["status"], "error")
            self.assertIsNone(log["completed_at"])
            other_lock = QLockFile(self.lock_path)
            other_lock.setStaleLockTime(0)
            self.assertFalse(other_lock.tryLock(0))
            return Mock()

        with (
            patch("detect_app.__main__.AppPaths.create", return_value=self.paths),
            patch("detect_app.__main__.QApplication") as application,
            patch("detect_app.__main__.ModelRegistry"),
            patch("detect_app.__main__.MainWindow", side_effect=create_window) as window,
        ):
            application.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
            window.assert_called_once()
        lock = QLockFile(self.lock_path)
        self.assertTrue(lock.tryLock(0))
        lock.unlock()


if __name__ == "__main__":
    unittest.main()
