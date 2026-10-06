import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from detect_app.config import AppPaths
from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.ui.window import MainWindow


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = AppPaths(root, root / "database.sqlite3", root, root, root, root, root, root)
        self.sessions = create_session_factory(self.paths.database)
        self.repository = AnalysisRepository(self.sessions)
        self.input = root / "A.png"
        self.history_image = root / "B.png"
        Image.new("RGB", (50, 50), "red").save(self.input)
        Image.new("RGB", (50, 50), "blue").save(self.history_image)
        self.repository.create_job("B", "B.png", "manual", "model", "a" * 64)
        self.repository.complete_job("B", [], "B.png")
        registry = Mock()
        registry.list_models.return_value = [SimpleNamespace(name="model", sha256="a" * 64, identifier="model")]
        self.service = Mock()
        self.service.resolve_image.side_effect = lambda path: root / path
        self.release = threading.Event()
        self.window = MainWindow(self.paths, self.repository, registry, self.service)
        self.window.show()

    def tearDown(self):
        self.release.set()
        if self.window._thread is not None:
            self.wait_until(lambda: self.window._thread is None)
        self.window.close()
        self.sessions.kw["bind"].dispose()
        self.temporary.cleanup()

    def wait_until(self, predicate):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(5000)
        loop.exec()
        poll.stop()
        timeout.stop()
        self.assertTrue(predicate(), "Timed out waiting for Qt lifecycle")

    def select_input(self):
        with patch("detect_app.ui.window.QFileDialog.getOpenFileName", return_value=(str(self.input), "")):
            self.window._select_image()

    def start_slow_analysis(self, failure=False):
        def analyze(source, model_id):
            if not self.release.wait(4):
                raise RuntimeError("Test worker timed out")
            if failure:
                raise RuntimeError("Test inference failure")
            self.repository.create_job("A", "A.png", "manual", "model", "a" * 64)
            self.repository.complete_job("A", [], "A.png")
            return "A"

        self.service.analyze.side_effect = analyze
        self.select_input()
        self.window._start_analysis()
        self.wait_until(lambda: self.service.analyze.called)

    def choose_close_option(self, deferred):
        def choose():
            dialog = self.application.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                role = QMessageBox.ButtonRole.AcceptRole if deferred else QMessageBox.ButtonRole.RejectRole
                for button in dialog.buttons():
                    if dialog.buttonRole(button) == role:
                        button.click()
                        return
            QTimer.singleShot(5, choose)

        QTimer.singleShot(0, choose)
        self.window.close()

    def test_idle_close_is_immediate(self):
        self.assertTrue(self.window.close())
        self.assertFalse(self.window.isVisible())

    def test_history_cannot_launch_analysis_or_replace_visible_input(self):
        self.select_input()
        self.window._tabs.setCurrentIndex(1)
        self.window._history.setCurrentRow(0)
        self.assertEqual(self.window._preview_path, self.history_image)
        self.assertEqual(self.window._selected_image, self.input)
        self.assertEqual(self.window._input_preview_path, self.input)
        self.assertFalse(self.window._analyze.isVisible())
        self.window._start_analysis()
        self.service.analyze.assert_not_called()
        self.window._tabs.setCurrentIndex(0)
        self.assertTrue(self.window._analyze.isVisible())
        self.assertEqual(self.window._input_image.pixmap().toImage().pixelColor(0, 0).name(), "#ff0000")
        self.start_slow_analysis()
        self.assertEqual(self.service.analyze.call_args.args[0].image_path(), self.input)

    def test_stay_keeps_window_open_and_thread_alive(self):
        self.start_slow_analysis()
        self.choose_close_option(False)
        self.assertFalse(self.window._close_when_finished)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._thread.isRunning())
        self.release.set()
        self.wait_until(lambda: self.window._thread is None)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._analyze.isEnabled())

    def test_deferred_close_waits_responsively_and_deletes_qt_objects(self):
        self.start_slow_analysis()
        deleted = []
        self.window._thread.destroyed.connect(lambda: deleted.append("thread"))
        self.window._worker.destroyed.connect(lambda: deleted.append("worker"))
        self.choose_close_option(True)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._thread.isRunning())
        self.assertFalse(self.window._analyze.isEnabled())
        self.window._start_analysis()
        self.assertEqual(self.service.analyze.call_count, 1)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        self.wait_until(lambda: bool(heartbeat))
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.close())
        self.release.set()
        self.wait_until(lambda: not self.window.isVisible() and len(deleted) == 2)
        self.assertIsNone(self.window._thread)
        self.assertIsNone(self.window._worker)
        self.window._start_analysis()
        self.assertEqual(self.service.analyze.call_count, 1)

    def test_deferred_close_also_handles_worker_failure(self):
        self.start_slow_analysis(failure=True)
        self.choose_close_option(True)
        self.release.set()
        self.wait_until(lambda: not self.window.isVisible())
        self.assertIsNone(self.window._thread)
        self.assertIsNone(self.window._worker)

    def test_analysis_finishing_during_close_dialog_still_closes_safely(self):
        self.start_slow_analysis()
        self.release.set()

        def close_after_thread():
            if self.window._thread is not None:
                QTimer.singleShot(5, close_after_thread)
                return
            dialog = self.application.activeModalWidget()
            for button in dialog.buttons():
                if dialog.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
                    button.click()
                    return

        QTimer.singleShot(0, close_after_thread)
        self.window.close()
        self.wait_until(lambda: not self.window.isVisible())
        self.assertIsNone(self.window._thread)

    def test_missing_history_image_is_not_restored_on_resize(self):
        self.window._show_preview(self.history_image)
        self.window._show_preview(self.paths.images / "missing.png")
        self.window._update_preview()
        self.assertIsNone(self.window._preview_path)
        self.assertEqual(self.window._image.text(), "Image introuvable")


if __name__ == "__main__":
    unittest.main()
