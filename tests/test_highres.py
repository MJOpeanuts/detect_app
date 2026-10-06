import gc
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from detect_app.ui.image_viewer import ImageViewer, _IMAGE_LOAD_POOL


@unittest.skipUnless(os.environ.get("DETECT_APP_RUN_HIGHRES") == "1", "set DETECT_APP_RUN_HIGHRES=1")
class HighResolutionPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=90):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        poll.start()
        timer.start(timeout * 1000)
        loop.exec()
        poll.stop()
        timer.stop()
        self.assertTrue(predicate(), "Timed out waiting for 12,000 × 9,000 preview")

    def test_compressed_108_megapixel_png_loads_as_bounded_preview(self):
        try:
            import resource

            peak_rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except ImportError:
            peak_rss_before = None

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "108mp.png"
            Image.new("L", (12_000, 9_000), 127).save(path, optimize=True)
            compressed_size = path.stat().st_size
            gc.collect()

            viewer = ImageViewer("empty")
            viewer.show()
            started = time.perf_counter()
            viewer.load_path(path)
            self.wait_until(lambda: not viewer.pixmap().isNull() or viewer.text() != "Chargement de l’image…")
            elapsed = time.perf_counter() - started

            self.assertFalse(viewer.pixmap().isNull(), viewer.text())
            self.assertEqual(viewer._source_size, (12_000, 9_000))
            self.assertLessEqual(max(viewer.pixmap().width(), viewer.pixmap().height()), 2048)
            self.assertTrue(viewer.preview_is_reduced)
            self.assertLess(compressed_size, 1_000_000)

            reference_box = {"id": "sample", "x_min": 6000, "y_min": 4500, "x_max": 6100, "y_max": 4600}
            viewer.set_detections([reference_box])
            mapped_image = viewer._image_item.mapRectToScene(viewer._image_item.boundingRect())
            self.assertAlmostEqual(mapped_image.width(), 12_000)
            self.assertAlmostEqual(mapped_image.height(), 9_000)
            self.assertEqual(viewer._overlay_items[0].rect().getRect(), (6000.0, 4500.0, 100.0, 100.0))

            try:
                peak_rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                peak_rss_delta_mib = max(0, peak_rss_after - peak_rss_before) / 1024
            except (NameError, UnboundLocalError):
                peak_rss_delta_mib = "indisponible"
            print(
                "High-resolution preview: source=12000x9000, "
                f"preview={viewer.pixmap().width()}x{viewer.pixmap().height()}, "
                f"file={compressed_size} bytes, load={elapsed:.2f}s, "
                f"peak_rss_delta={peak_rss_delta_mib} MiB"
            )
            viewer.close()
            self.assertTrue(_IMAGE_LOAD_POOL.waitForDone(5000))


if __name__ == "__main__":
    unittest.main()
