import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from PIL import Image

from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.image_source import ManualImageSource
from detect_app.services.models import ModelRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class AnalysisServiceTests(unittest.TestCase):
    def test_selected_confidence_threshold_reaches_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = ModelRegistry(root / "models", root / "configuration")
            model = registry.ensure_bundled_model(REPOSITORY_ROOT / "ic_detect_best.onnx")
            image_path = root / "input.png"
            Image.new("RGB", (20, 20), "white").save(image_path)
            repository = AnalysisRepository(create_session_factory(root / "database.sqlite3"))
            service = AnalysisService(repository, registry, root / "analyses")

            with patch(
                "detect_app.services.analysis.run_inference",
                return_value=(Image.new("RGB", (20, 20), "white"), []),
            ) as inference:
                service.analyze(ManualImageSource(image_path), model.identifier, 0.73)

            self.assertEqual(inference.call_args.args[3], 0.73)

    def test_analysis_and_preview_decoding_are_serialized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "input.png"
            preview_path = root / "preview.png"
            Image.new("RGB", (20, 20), "white").save(source_path)
            Image.new("RGB", (20, 20), "blue").save(preview_path)
            registry = Mock()
            registry.get.return_value = SimpleNamespace(
                name="model", sha256="a" * 64, path=root / "model.onnx", class_names=("part",)
            )
            service = AnalysisService(Mock(), registry, root / "analyses")
            inference_started = threading.Event()
            release_inference = threading.Event()
            preview_finished = threading.Event()

            def slow_inference(*_args):
                inference_started.set()
                if not release_inference.wait(5):
                    raise RuntimeError("Test inference timed out")
                return Image.new("RGB", (20, 20), "white"), []

            def load_preview():
                from detect_app.vision.image import make_preview

                make_preview(preview_path)
                preview_finished.set()

            with patch("detect_app.services.analysis.run_inference", side_effect=slow_inference):
                analysis = threading.Thread(
                    target=service.analyze,
                    args=(ManualImageSource(source_path), "model"),
                )
                analysis.start()
                self.assertTrue(inference_started.wait(2))
                preview = threading.Thread(target=load_preview)
                preview.start()
                try:
                    self.assertFalse(preview_finished.wait(0.1))
                finally:
                    release_inference.set()
                analysis.join(5)
                preview.join(5)

            self.assertFalse(analysis.is_alive())
            self.assertFalse(preview.is_alive())
            self.assertTrue(preview_finished.is_set())

    def test_repeated_image_and_model_create_distinct_completed_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = ModelRegistry(root / "models", root / "configuration")
            model = registry.ensure_bundled_model(REPOSITORY_ROOT / "ic_detect_best.onnx")
            image_path = root / "blank.png"
            Image.new("RGB", (320, 240), "white").save(image_path)
            repository = AnalysisRepository(create_session_factory(root / "database.sqlite3"))
            service = AnalysisService(repository, registry, root / "analyses")

            first_job = service.analyze(ManualImageSource(image_path), model.identifier)
            second_job = service.analyze(ManualImageSource(image_path), model.identifier)

            self.assertNotEqual(first_job, second_job)
            self.assertEqual(len(repository.list_jobs()), 2)
            for job_id in (first_job, second_job):
                log, objects = repository.get_job(job_id)
                self.assertEqual(log["status"], "completed")
                self.assertEqual(objects, [])
                self.assertTrue(service.resolve_image(log["annotated_image_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
