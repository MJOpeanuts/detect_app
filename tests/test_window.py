import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEventLoop, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QGroupBox, QMessageBox, QPushButton, QRadioButton

from detect_app.config import AppPaths
from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.persistence.repository import UNASSIGNED
from detect_app.services.models import ModelSpec
from detect_app.vision.types import Detection
from detect_app.ui.classification import ClassificationDialog
from detect_app.ui.image_viewer import _IMAGE_LOAD_POOL, ImageViewer
from detect_app.ui.tasks import wait_for_tasks
from detect_app.ui.window import ANNOTATED, ORIGINAL, MainWindow


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = AppPaths(
            root,
            root / "database.sqlite3",
            root,
            root,
            root,
            root,
            root,
            Path(__file__).resolve().parents[1] / "nuts-app.png",
        )
        self.sessions = create_session_factory(self.paths.database)
        self.repository = AnalysisRepository(self.sessions)
        self.input = root / "A.png"
        self.history_image = root / "B.png"
        Image.new("RGB", (50, 50), "red").save(self.input)
        Image.new("RGB", (50, 50), "blue").save(self.history_image)
        self.crop_image = root / "crop.png"
        Image.new("RGB", (12, 12), "green").save(self.crop_image)
        self.repository.create_job("B", "B.png", "manual", "model", "a" * 64)
        self.repository.complete_job("B", [], "B.png")
        self.root = root
        self.models = [self.make_model("model")]
        self.registry = Mock()
        self.registry.list_models.side_effect = lambda: list(self.models)
        self.service = Mock()
        self.service.resolve_image.side_effect = lambda path: root / path
        self.release = threading.Event()
        self.window = MainWindow(self.paths, self.repository, self.registry, self.service)
        self.window.show()
        self.wait_until(lambda: self.window._models_loaded)

    def make_model(self, identifier):
        return ModelSpec(identifier, identifier, self.root / f"{identifier}.onnx", "a" * 64, ("part",), 640)

    def tearDown(self):
        self.release.set()
        wait_for_tasks(5000)
        _IMAGE_LOAD_POOL.waitForDone(5000)
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

    def select_input(self, path=None):
        path = path or self.input
        with patch("detect_app.ui.window.QFileDialog.getOpenFileName", return_value=(str(path), "")):
            self.window._select_image()
        self.wait_until(lambda: self.window._selected_image == path and not self.window._import_probe.pending)

    def start_slow_analysis(self, failure=False):
        def analyze(source, model_id, confidence_threshold, pcba_id=None):
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
        self.wait_until(lambda: not self.window._input_image.pixmap().isNull())
        self.window._tabs.setCurrentIndex(1)
        self.window._history.setCurrentRow(0)
        self.wait_until(lambda: not self.window._image.pixmap().isNull())
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
        self.wait_until(lambda: not self.window._image.pixmap().isNull())
        self.window._show_preview(self.paths.images / "missing.png")
        self.window._update_preview()
        self.assertIsNone(self.window._preview_path)
        self.assertEqual(self.window._image.text(), "Image introuvable")

    def test_unreadable_history_image_is_shown_as_missing_without_crashing(self):
        unreadable = self.paths.images / "unreadable.png"
        unreadable.write_text("not an image", encoding="utf-8")
        self.window._show_preview(unreadable)
        self.wait_until(lambda: self.window._image.text() != "Chargement de l’image…")
        self.assertIn("Fichier image corrompu ou illisible", self.window._image.text())

    def test_image_change_clears_analysis_result_but_preserves_history(self):
        self.select_input()
        self.window._results.set_result([self.make_object("old")], "1 objet détecté")
        self.select_input(self.history_image)
        self.assertEqual(self.window._results.table.rowCount(), 0)
        self.assertEqual(self.window._input_image.path, self.history_image)
        self.assertEqual(len(self.repository.list_jobs()), 1)

    def test_confidence_value_is_passed_to_worker_and_result_is_visible(self):
        self.window._confidence.setValue(42.5)
        self.start_slow_analysis()
        self.assertAlmostEqual(self.service.analyze.call_args.args[2], 0.425)
        self.release.set()
        self.wait_until(lambda: self.window._thread is None)
        self.assertEqual(self.window._results.summary.text(), "Analyse terminée : aucun objet détecté.")
        self.assertIn("aucun objet détecté", self.window._active_status.text())

    def test_worker_failure_clears_previous_image_overlays_and_objects(self):
        self.select_input()
        previous = self.make_object("previous")
        self.window._input_image.set_detections([previous])
        self.window._results.set_result([previous], "1 objet détecté")

        def fail_before_job(_source, _model_id, _threshold, pcba_id=None):
            if not self.release.wait(4):
                raise RuntimeError("Test worker timed out")
            raise RuntimeError("Test inference failure")

        self.service.analyze.side_effect = fail_before_job
        self.window._start_analysis()
        self.wait_until(lambda: self.service.analyze.called)
        self.assertEqual(self.window._results.table.rowCount(), 0)
        self.assertEqual(self.window._input_image._detections, [])
        self.release.set()
        self.wait_until(lambda: self.window._thread is None)
        self.assertEqual(self.window._input_image._detections, [])

    def test_history_selection_does_not_overwrite_active_status_or_infer(self):
        self.select_input()
        self.window._set_active_status("Analyse en cours…")
        self.window._tabs.setCurrentIndex(1)
        self.window._history.setCurrentRow(0)
        self.assertEqual(self.window._active_status.text(), "Analyse en cours…")
        self.service.analyze.assert_not_called()
        self.assertEqual(self.window._selected_image, self.input)

    def make_object(self, identity, confidence=0.5, crop_path="crop.png"):
        return {
            "id": identity,
            "class_id": 0,
            "class_name": f"objet-{identity}",
            "confidence": confidence,
            "x_min": 2.0,
            "y_min": 3.0,
            "x_max": 18.0,
            "y_max": 19.0,
            "crop_path": crop_path,
        }

    def test_selection_after_sort_keeps_detection_and_crop_identity(self):
        objects = [self.make_object("high", 1.0), self.make_object("low", 0.95)]
        self.window._input_image.load_path(self.input)
        self.window._input_image.set_detections(objects)
        self.window._results.set_result(objects, "2 objets détectés")
        table = self.window._results.table
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual(table.item(0, 1).text(), "100.0%")
        self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole), "high")
        self.assertEqual(table.item(1, 1).text(), "95.0%")
        row = next(
            row for row in range(table.rowCount())
            if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "low"
        )
        table.selectRow(row)
        self.assertEqual(self.window._results.selected_id(), "low")
        self.assertEqual(self.window._input_image._selected_id, "low")
        self.assertEqual(self.window._results.crop.path, self.crop_image)
        self.assertEqual(self.window._results.table.columnCount(), 2)
        self.assertEqual(self.window.windowTitle(), "detect_app")

    def test_fullscreen_shortcuts_toggle_and_restore_window_geometry(self):
        geometry = self.window.geometry()
        QTest.keyClick(self.window, Qt.Key.Key_F11)
        self.assertTrue(self.window.isFullScreen())
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(self.window.geometry(), geometry)

    def test_brand_resources_are_present_and_loadable(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue(QIcon(str(root / "nuts-app.ico")).isNull() is False)
        self.assertTrue((root / "nuts-app.png").is_file())
        self.assertTrue((root / "powered by_white.png").is_file())
        self.assertTrue((root / "squirrel.svg").is_file())
        self.assertTrue((root / "THIRD_PARTY_NOTICES.md").is_file())
        self.assertFalse(self.window._analyze.icon().isNull())
        self.assertFalse(self.window._footer.findChild(type(self.window._input_name), "brandAttribution").pixmap().isNull())


    # ---------------------------------------------------------------- layout

    def test_exactly_three_tabs_in_order_without_model_management_in_analysis(self):
        tabs = self.window._tabs
        self.assertEqual([tabs.tabText(index) for index in range(tabs.count())], ["Analyse", "Historique", "Modèles"])
        analysis_buttons = [button.text() for button in tabs.widget(0).findChildren(QPushButton)]
        self.assertFalse([text for text in analysis_buttons if "modèle" in text.lower()])
        self.assertIn("Importer une image", analysis_buttons)
        self.assertIn("Ajouter un modèle ONNX", [b.text() for b in tabs.widget(2).findChildren(QPushButton)])
        self.assertEqual(self.window._confidence.value(), 25.0)
        self.assertEqual((self.window._confidence.minimum(), self.window._confidence.maximum()), (0.0, 100.0))
        self.assertIsNone(self.window._client_combo.currentData())
        self.assertIsNone(self.window._pcba_combo.currentData())

    def test_analyze_disabled_with_hint_when_no_compatible_model(self):
        self.models.clear()
        self.window._refresh_models()
        self.wait_until(lambda: self.window._models.count() == 0)
        self.select_input()
        self.assertFalse(self.window._analyze.isEnabled())
        self.assertTrue(self.window._no_model_hint.isVisible())
        self.window._no_model_hint.linkActivated.emit("models")
        self.assertEqual(self.window._tabs.currentIndex(), 2)

    def test_diagnostic_is_a_collapsed_chevron_panel_without_checkbox(self):
        self.assertEqual(self.window.findChildren(QCheckBox), [])
        self.assertEqual(self.window.findChildren(QRadioButton), [])
        self.assertFalse(any(box.isCheckable() for box in self.window.findChildren(QGroupBox)))
        for panel in (self.window._results, self.window._history_results, self.window._models_page):
            diagnostic = panel.diagnostic
            self.assertEqual(diagnostic.header.text(), "Diagnostic")
            self.assertFalse(diagnostic.is_expanded())
            self.assertFalse(diagnostic.content.isVisibleTo(self.window))
            self.assertEqual(diagnostic.header.arrowType(), Qt.ArrowType.RightArrow)
        diagnostic = self.window._results.diagnostic
        self.assertLess(diagnostic.sizeHint().height(), 40)
        self.assertEqual(diagnostic.header.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        diagnostic.header.setFocus()
        QTest.keyClick(diagnostic.header, Qt.Key.Key_Space)
        self.assertTrue(diagnostic.is_expanded())
        self.assertEqual(diagnostic.header.arrowType(), Qt.ArrowType.DownArrow)
        self.assertTrue(diagnostic.content.isVisible())
        self.assertTrue(diagnostic.content.isReadOnly())
        self.assertTrue(diagnostic.content.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        diagnostic.header.click()
        self.assertFalse(diagnostic.content.isVisible())

    def test_error_details_survive_object_selection_and_summary_stays_visible(self):
        panel = self.window._results
        objects = [self.make_object("one"), self.make_object("two")]
        panel.set_result(objects, "Échec partiel", "OSError: disque plein\nTraceback détaillé")
        self.assertTrue(panel.error_summary.isVisible())
        self.assertEqual(panel.error_summary.text(), "OSError: disque plein")
        panel.table.selectRow(1)
        self.assertIn("Traceback détaillé", panel.diagnostic.section(panel.ERROR_SECTION))
        self.assertIn("x1=2.0", panel.diagnostic.section(panel.OBJECT_SECTION))
        self.assertIn("Traceback détaillé", panel.diagnostic.text())
        self.assertIn("x1=2.0", panel.diagnostic.text())

    def test_footer_logo_sits_on_application_background_without_grey_rectangle(self):
        footer = self.window._footer
        self.assertFalse(footer.autoFillBackground())
        label = footer.findChild(type(self.window._input_name), "brandAttribution")
        self.assertFalse(label.autoFillBackground())
        self.assertLessEqual(footer.height(), 32)
        self.window.resize(1280, 720)
        QApplication.processEvents()
        frame = self.window.grab().toImage()
        ratio = frame.devicePixelRatio()
        for widget, point in ((footer, QPoint(3, 3)), (footer, QPoint(footer.width() - 3, footer.height() // 2)),
                              (label, QPoint(1, 1))):
            position = widget.mapTo(self.window, point)
            color = frame.pixelColor(int(position.x() * ratio), int(position.y() * ratio))
            self.assertEqual(color.name(), "#181a1d")
        for index in range(self.window._tabs.count()):
            self.window._tabs.setCurrentIndex(index)
            self.assertTrue(footer.isVisible())
        QTest.keyClick(self.window, Qt.Key.Key_F11)
        self.assertTrue(footer.isVisible())
        QTest.keyClick(self.window, Qt.Key.Key_Escape)

    # ---------------------------------------------------------------- import

    def mime(self, *urls):
        data = QMimeData()
        data.setUrls([url if isinstance(url, QUrl) else QUrl.fromLocalFile(str(url)) for url in urls])
        return data

    def drop(self, mime):
        viewer = self.window._input_image
        event = QDropEvent(
            QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
        )
        viewer.dropEvent(event)

    def test_dialog_import_loads_without_analysis(self):
        self.select_input()
        self.assertEqual(self.window._input_name.text(), "A.png")
        self.assertEqual(self.window._input_image.path, self.input)
        self.assertTrue(self.window._analyze.isEnabled())
        self.service.analyze.assert_not_called()

    def test_drag_and_drop_import_with_hover_feedback_and_no_auto_analysis(self):
        viewer = self.window._input_image
        self.assertTrue(viewer.acceptDrops())
        hovered = self.mime(self.input)  # the event does not own its mime data
        enter = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, hovered,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )
        viewer.dragEnterEvent(enter)
        self.assertTrue(viewer.is_drop_active())
        viewer.dragLeaveEvent(QDragLeaveEvent())
        self.assertFalse(viewer.is_drop_active())
        self.drop(self.mime(self.input))
        self.wait_until(lambda: self.window._selected_image == self.input)
        self.assertFalse(viewer.is_drop_active())
        self.assertEqual(viewer.path, self.input)
        self.service.analyze.assert_not_called()

    def test_dialog_and_drop_share_the_same_validation(self):
        with patch("detect_app.ui.window.check_local_image_file", wraps=__import__(
            "detect_app.services.image_source", fromlist=["check_local_image_file"]
        ).check_local_image_file) as check:
            self.select_input()
            self.drop(self.mime(self.history_image))
            self.wait_until(lambda: self.window._selected_image == self.history_image)
        self.assertEqual([call.args[0] for call in check.call_args_list], [self.input, self.history_image])

    def assert_refused(self, mime, fragment):
        self.drop(mime)
        QTest.qWait(50)
        _IMAGE_LOAD_POOL.waitForDone(5000)
        self.wait_until(lambda: not self.window._import_probe.pending)
        QApplication.processEvents()
        self.assertIn(fragment, self.window._source_note.text())
        self.assertEqual(self.window._selected_image, self.input)
        self.assertEqual(self.window._results.table.rowCount(), 1)

    def test_invalid_drops_are_refused_without_clearing_valid_result(self):
        self.select_input()
        self.window._results.set_result([self.make_object("kept")], "1 objet détecté")
        corrupt = self.root / "corrompu.png"
        corrupt.write_text("pas une image", encoding="utf-8")
        folder = self.root / "dossier"
        folder.mkdir()
        self.assert_refused(self.mime(self.input, self.history_image), "une seule image")
        self.assert_refused(self.mime(QUrl("https://example.com/a.png")), "URL distante")
        self.assert_refused(self.mime(folder), "dossier")
        self.assert_refused(self.mime(corrupt), "corrompu.png")
        self.assertFalse(self.window._drop_is_acceptable(self.mime(corrupt.with_name("absent.png"))))
        self.assertFalse(self.window._drop_is_acceptable(self.mime(self.input, self.history_image)))
        self.assertTrue(self.window._drop_is_acceptable(self.mime(self.history_image)))
        self.service.analyze.assert_not_called()

    def test_import_refused_during_analysis(self):
        self.start_slow_analysis()
        self.assertFalse(self.window._choose_image.isEnabled())
        self.assertFalse(self.window._client_combo.isEnabled())
        self.assertFalse(self.window._pcba_combo.isEnabled())
        self.assertFalse(self.window._drop_is_acceptable(self.mime(self.history_image)))
        self.drop(self.mime(self.history_image))
        QTest.qWait(50)
        self.assertIn("pendant l’analyse", self.window._source_note.text())
        self.assertEqual(self.window._selected_image, self.input)
        self.assertEqual(self.window._input_image.path, self.input)

    # ---------------------------------------------------------------- results

    def make_annotated_job(self, job_id, with_original=True, with_annotated=True, pcba_id=None):
        original = self.root / f"{job_id}.png"
        annotated = self.root / f"{job_id}_annotated.png"
        if with_original:
            Image.new("RGB", (50, 50), "blue").save(original)
        if with_annotated:
            Image.new("RGB", (50, 50), "yellow").save(annotated)
        self.repository.create_job(job_id, original.name, "manual", "model", "a" * 64, pcba_id)
        self.repository.complete_job(
            job_id, [Detection(0, "ic", 0.9, 5, 5, 30, 30, "crop.png")], annotated.name
        )
        return original, annotated

    def test_annotated_result_is_shown_immediately_after_success(self):
        def analyze(source, model_id, threshold, pcba_id=None):
            self.make_annotated_job("J")
            return "J"

        self.service.analyze.side_effect = analyze
        self.select_input()
        self.window._start_analysis()
        self.wait_until(lambda: self.window._thread is None)
        self.wait_until(lambda: not self.window._input_image.pixmap().isNull())
        viewer = self.window._input_image
        self.assertEqual(viewer.path, self.root / "J_annotated.png")
        self.assertEqual(self.window._input_state.text(), "Image annotée")
        self.assertEqual(viewer.overlay_mode, ImageViewer.OVERLAY_SELECTION)
        self.assertEqual(self.window._results.table.rowCount(), 1)
        self.assertEqual(viewer.overlay_count(), 1)
        self.assertEqual(viewer.pixmap().toImage().pixelColor(0, 0).name(), "#ffff00")
        self.assertEqual(self.window._results.crop.path, self.crop_image)
        self.assertEqual(self.window._tabs.currentIndex(), 0)
        self.assertEqual(Image.open(self.input).getpixel((0, 0)), (255, 0, 0))

    def select_history(self, job_id):
        self.window._tabs.setCurrentIndex(1)
        self.window._refresh_history()
        for row in range(self.window._history.count()):
            if self.window._history.item(row).data(Qt.ItemDataRole.UserRole) == job_id:
                self.window._history.setCurrentRow(row)
                break
        self.wait_until(lambda: not self.window._image.pixmap().isNull() or self.window._image.text() not in (
            "", "Chargement de l’image…"
        ))

    def test_history_original_and_annotated_modes_switch_without_inference_or_reload(self):
        original, annotated = self.make_annotated_job("C")
        self.select_history("C")
        viewer = self.window._image
        self.assertTrue(self.window._mode_buttons[ANNOTATED].isChecked())
        self.assertEqual(viewer.path, annotated)
        self.assertEqual(viewer.overlay_mode, ImageViewer.OVERLAY_SELECTION)
        self.assertEqual(viewer.overlay_count(), 1)
        viewer.scale(2, 2)
        zoom = viewer.transform().m11()
        self.window._mode_buttons[ORIGINAL].click()
        self.wait_until(lambda: not viewer.pixmap().isNull())
        self.assertEqual(viewer.path, original)
        self.assertEqual(viewer.overlay_count(), 0)
        self.assertEqual(viewer.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff")
        self.assertAlmostEqual(viewer.transform().m11(), zoom)
        with patch("detect_app.ui.image_viewer.ImageLoadTask") as task:
            self.window._mode_buttons[ANNOTATED].click()
            self.window._mode_buttons[ORIGINAL].click()
            self.window._mode_buttons[ANNOTATED].click()
        task.assert_not_called()
        self.assertEqual(viewer.overlay_count(), 1)
        self.service.analyze.assert_not_called()
        self.assertIsNone(self.window._selected_image)
        self.assertEqual(self.window._input_image.path, None)

    def test_history_missing_annotated_keeps_original_with_local_warning(self):
        original, annotated = self.make_annotated_job("D", with_annotated=False)
        self.select_history("D")
        self.assertFalse(self.window._mode_buttons[ANNOTATED].isEnabled())
        self.assertTrue(self.window._mode_buttons[ORIGINAL].isChecked())
        self.assertEqual(self.window._image.path, original)
        self.assertEqual(self.window._image.overlay_count(), 0)
        self.assertIn("Image annotée introuvable", self.window._history_warning.text())
        self.assertIn(str(annotated), self.window._history_results.diagnostic.section("Fichiers"))
        self.assertEqual(self.window._history_results.table.rowCount(), 1)
        self.service.analyze.assert_not_called()

    def test_history_missing_original_still_shows_annotated(self):
        _original, annotated = self.make_annotated_job("E", with_original=False)
        self.select_history("E")
        self.assertFalse(self.window._mode_buttons[ORIGINAL].isEnabled())
        self.assertTrue(self.window._mode_buttons[ANNOTATED].isChecked())
        self.assertEqual(self.window._image.path, annotated)
        self.assertIn("Image d’origine introuvable", self.window._history_warning.text())
        self.assertTrue(self.window._open_folder.isEnabled())

    # ---------------------------------------------------------------- models

    def test_added_model_is_immediately_available_in_analysis(self):
        self.select_input()
        source = self.root / "nouveau.onnx"
        source.write_bytes(b"onnx")
        self.registry.inspect_candidate.return_value = SimpleNamespace(class_names=("part",), class_count=1)

        def register(path, names):
            model = self.make_model("nouveau")
            self.models.append(model)
            return model

        self.registry.register.side_effect = register
        self.window._tabs.setCurrentIndex(2)
        with patch("detect_app.ui.window.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.window._models_page.add_button.click()
        self.wait_until(lambda: self.window._models.findData("nouveau") >= 0)
        self.assertEqual(self.registry.register.call_args.args, (source, ("part",)))
        self.assertEqual(self.window._models.currentData(), "nouveau")
        self.assertEqual(self.window._models_page.current_identifier(), "nouveau")
        self.assertEqual(self.window._selected_image, self.input)
        self.assertEqual(self.window._input_image.path, self.input)

    def test_model_removal_requires_confirmation_and_keeps_history(self):
        self.registry.remove.side_effect = lambda identifier: self.models.clear() or []
        self.window._tabs.setCurrentIndex(2)
        with patch("detect_app.ui.window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.window._remove_model("model")
        self.registry.remove.assert_not_called()
        with patch("detect_app.ui.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as ask:
            self.window._remove_model("model")
        self.assertIn("fichier ONNX que vous aviez fourni", ask.call_args.args[2])
        self.wait_until(lambda: self.window._models.count() == 0)
        self.assertEqual(len(self.repository.list_jobs()), 1)

    def test_model_used_by_running_analysis_cannot_be_removed(self):
        self.start_slow_analysis()
        with patch("detect_app.ui.window.QMessageBox.information") as info:
            self.window._remove_model("model")
        info.assert_called_once()
        self.registry.remove.assert_not_called()

    # ---------------------------------------------------------------- classification

    def test_selected_pcba_is_passed_to_analysis_and_locked_while_running(self):
        client = self.repository.create_client("ACME")
        pcba = self.repository.create_pcba("Carte mère", client["id"])
        self.window._refresh_classification()
        self.window._pcba_combo.setCurrentIndex(self.window._pcba_combo.findData(pcba["id"]))
        self.assertEqual(self.window._client_combo.currentData(), client["id"])
        self.assertIn("ACME", self.window._classification_hint.text())
        self.start_slow_analysis()
        self.assertEqual(self.service.analyze.call_args.kwargs["pcba_id"], pcba["id"])
        self.assertFalse(self.window._pcba_combo.isEnabled())
        self.assertFalse(self.window._new_pcba.isEnabled())

    def test_client_without_pcba_is_not_recorded(self):
        client = self.repository.create_client("ACME")
        self.window._refresh_classification()
        self.window._client_combo.setCurrentIndex(self.window._client_combo.findData(client["id"]))
        self.assertIn("non classé", self.window._classification_hint.text())
        self.start_slow_analysis()
        self.assertIsNone(self.service.analyze.call_args.kwargs["pcba_id"])

    def test_history_filters_and_labels_distinguish_missing_pcba_and_client(self):
        client = self.repository.create_client("ACME")
        with_client = self.repository.create_pcba("P1", client["id"])
        orphan = self.repository.create_pcba("P2")
        self.make_annotated_job("F", pcba_id=with_client["id"])
        self.make_annotated_job("G", pcba_id=orphan["id"])
        self.window._fill_history_filters()
        self.window._tabs.setCurrentIndex(1)
        self.window._refresh_history()
        texts = [self.window._history.item(row).text() for row in range(self.window._history.count())]
        self.assertEqual(len(texts), 3)
        self.assertTrue(any("ACME / P1" in text for text in texts))
        self.assertTrue(any("Sans client / P2" in text for text in texts))
        self.assertTrue(any("Sans PCBA" in text for text in texts))
        clients = self.window._history_client_filter
        clients.setCurrentIndex(clients.findData(client["id"]))
        self.assertEqual(self.window._history.count(), 1)
        self.assertEqual(self.window._history_pcba_filter.findData(UNASSIGNED), -1)
        self.assertEqual(self.window._history_pcba_filter.findData(orphan["id"]), -1)
        clients.setCurrentIndex(clients.findData(UNASSIGNED))
        pcbas = self.window._history_pcba_filter
        pcbas.setCurrentIndex(pcbas.findData(UNASSIGNED))
        ids = [self.window._history.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.window._history.count())]
        self.assertEqual(ids, ["B"])
        self.service.analyze.assert_not_called()

    def test_classification_dialog_relinks_job_and_confirms_client_change(self):
        client = self.repository.create_client("ACME")
        other = self.repository.create_client("Beta")
        pcba = self.repository.create_pcba("P1", client["id"])
        self.make_annotated_job("H", pcba_id=pcba["id"])
        log, _ = self.repository.get_job("B")
        dialog = ClassificationDialog(self.repository, log, self.window)
        dialog.pcba.setCurrentIndex(dialog.pcba.findData(pcba["id"]))
        self.assertEqual(dialog.client.currentData(), client["id"])
        dialog.client.setCurrentIndex(dialog.client.findData(other["id"]))
        self.assertIn("toutes les analyses", dialog.note.text())
        with patch("detect_app.ui.classification.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No) as ask:
            dialog.accept()
        self.assertIn("2 analyse(s)", ask.call_args.args[2])
        self.assertIsNone(self.repository.get_job("B")[0]["pcba_id"])
        dialog.client.setCurrentIndex(dialog.client.findData(client["id"]))
        dialog.accept()
        self.assertEqual(self.repository.get_job("B")[0]["pcba_id"], pcba["id"])
        self.assertTrue(dialog.changed)
        self.assertEqual(self.repository.get_job("B")[0]["image_path"], "B.png")

    def test_classification_cannot_be_edited_for_processing_job(self):
        self.repository.create_job("P", "A.png", "manual", "model", "a" * 64)
        self.select_history("P")
        self.assertFalse(self.window._edit_classification.isEnabled())


if __name__ == "__main__":
    unittest.main()
