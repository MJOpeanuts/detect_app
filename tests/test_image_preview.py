import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from detect_app.ui.image_viewer import ImageViewer, _IMAGE_LOAD_POOL
from detect_app.vision.image import (
    CorruptImageError,
    ImageMemoryLimitError,
    ImageTooLargeError,
    UnsupportedImageError,
    make_preview,
)


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        _IMAGE_LOAD_POOL.waitForDone(5000)
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=5):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        poll.start()
        timer.start(timeout * 1000)
        loop.exec()
        poll.stop()
        timer.stop()
        self.assertTrue(predicate(), "Timed out waiting for preview")

    def test_preview_preserves_source_dimensions_for_non_square_image(self):
        path = self.root / "rectangle.png"
        Image.new("RGB", (80, 40), "red").save(path)

        preview, reference_size = make_preview(path)

        self.assertEqual(reference_size, (80, 40))
        self.assertEqual(preview.size, (80, 40))

    def test_preview_applies_exif_orientation_and_keeps_original_file_unchanged(self):
        path = self.root / "oriented.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (50, 30), "red").save(path, exif=exif)
        original_bytes = path.read_bytes()

        preview, reference_size = make_preview(path)

        self.assertEqual(reference_size, (30, 50))
        self.assertEqual(preview.size, (30, 50))
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_rounded_preview_dimensions_map_back_to_source_coordinates(self):
        path = self.root / "rounded.png"
        Image.new("L", (4001, 3001), 127).save(path)
        viewer = ImageViewer("empty")
        viewer.load_path(path)
        self.wait_until(lambda: not viewer.pixmap().isNull())
        viewer.set_detections(
            [{"id": "box", "x_min": 1000, "y_min": 1200, "x_max": 1100, "y_max": 1300}]
        )

        mapped_image = viewer._image_item.mapRectToScene(viewer._image_item.boundingRect())

        self.assertEqual((viewer.pixmap().width(), viewer.pixmap().height()), (2048, 1536))
        self.assertEqual((mapped_image.width(), mapped_image.height()), (4001.0, 3001.0))
        self.assertEqual(viewer._overlay_items[0].rect().getRect(), (1000.0, 1200.0, 100.0, 100.0))
        viewer.close()

    def test_supported_formats_and_distinct_read_errors(self):
        image = Image.new("RGB", (10, 5), "blue")
        for extension, image_format in (
            (".bmp", "BMP"),
            (".jpg", "JPEG"),
            (".png", "PNG"),
            (".tiff", "TIFF"),
            (".webp", "WEBP"),
        ):
            with self.subTest(format=image_format):
                path = self.root / f"image{extension}"
                image.save(path, format=image_format)
                preview, _ = make_preview(path)
                self.assertEqual(preview.size, (10, 5))

        corrupt = self.root / "corrupt.png"
        corrupt.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        with self.assertRaises(CorruptImageError):
            make_preview(corrupt)
        unsupported = self.root / "unsupported.gif"
        image.save(unsupported, format="GIF")
        with self.assertRaises(UnsupportedImageError):
            make_preview(unsupported)
        with self.assertRaises(FileNotFoundError):
            make_preview(self.root / "missing.png")

    def test_image_pixel_and_estimated_memory_limits_are_explicit(self):
        path = self.root / "small.png"
        Image.new("RGB", (20, 10), "white").save(path)
        with patch("detect_app.vision.image.MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(ImageTooLargeError):
                make_preview(path)
        with patch("detect_app.vision.image.MAX_ESTIMATED_IMAGE_MEMORY", 1024):
            with self.assertRaises(ImageMemoryLimitError):
                make_preview(path)

    def test_stale_concurrent_requests_cannot_replace_latest_preview_or_overlays(self):
        paths = {name: self.root / name for name in ("A.png", "B.png", "C.png")}
        for path in paths.values():
            Image.new("RGB", (30, 20), "white").save(path)
        started = threading.Event()
        release = threading.Event()
        colors = {"A.png": "red", "B.png": "green", "C.png": "blue"}

        def delayed_preview(path):
            if Path(path).name == "A.png":
                started.set()
                release.wait(5)
            return Image.new("RGB", (30, 20), colors[Path(path).name]), (30, 20)

        viewer = ImageViewer("empty")
        viewer.show()
        with patch("detect_app.ui.image_viewer.make_preview", side_effect=delayed_preview):
            viewer.load_path(paths["A.png"])
            self.assertTrue(started.wait(2))
            viewer.load_path(paths["B.png"])
            viewer.load_path(paths["C.png"])
            viewer.set_detections(
                [{"id": "C", "x_min": 2, "y_min": 3, "x_max": 8, "y_max": 9}]
            )
            release.set()
            self.wait_until(lambda: not viewer.pixmap().isNull())
            _IMAGE_LOAD_POOL.waitForDone(5000)

        self.assertEqual(viewer.path, paths["C.png"])
        self.assertEqual(viewer.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff")
        self.assertEqual(len(viewer._overlay_items), 1)
        self.assertEqual(viewer._overlay_items[0].rect().getRect(), (2.0, 3.0, 6.0, 6.0))
        viewer.close()

    def test_destroyed_viewer_does_not_receive_preview_signal(self):
        path = self.root / "slow.png"
        Image.new("RGB", (30, 20), "white").save(path)
        started = threading.Event()
        release = threading.Event()

        def delayed_preview(_path):
            started.set()
            release.wait(5)
            return Image.new("RGB", (30, 20), "red"), (30, 20)

        viewer = ImageViewer("empty")
        with patch("detect_app.ui.image_viewer.make_preview", side_effect=delayed_preview):
            viewer.load_path(path)
            self.assertTrue(started.wait(2))
            viewer.deleteLater()
            self.application.sendPostedEvents()
            self.application.processEvents()
            release.set()
            self.assertTrue(_IMAGE_LOAD_POOL.waitForDone(5000))
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
