import shutil
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
from detect_app.vision.types import Detection


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


    def make_service(self, root):
        registry = ModelRegistry(root / "models", root / "configuration")
        model = registry.ensure_bundled_model(REPOSITORY_ROOT / "ic_detect_best.onnx")
        repository = AnalysisRepository(create_session_factory(root / "database.sqlite3"))
        return registry, model, repository, AnalysisService(repository, registry, root / "analyses")

    def test_classification_is_kept_when_analysis_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _registry, model, repository, service = self.make_service(root)
            pcba = repository.create_pcba("Carte", repository.create_client("ACME")["id"])
            image_path = root / "input.png"
            Image.new("RGB", (20, 20), "white").save(image_path)
            with patch("detect_app.services.analysis.run_inference", side_effect=RuntimeError("panne")):
                job_id = service.analyze(ManualImageSource(image_path), model.identifier, 0.25, pcba_id=pcba["id"])
            log, _objects = repository.get_job(job_id)
            self.assertEqual(log["status"], "error")
            self.assertEqual(log["pcba_id"], pcba["id"])
            self.assertEqual(log["client_name"], "ACME")

    def test_classification_does_not_change_inference_or_job_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _registry, model, repository, service = self.make_service(root)
            pcba = repository.create_pcba("Carte")
            image_path = root / "input.png"
            Image.new("RGB", (40, 30), "white").save(image_path)
            detection = Detection(0, "ic", 0.9, 2, 3, 20, 25)
            calls = []

            def inference(*args):
                calls.append(args[2:])
                return Image.new("RGB", (40, 30), "white"), [detection]

            with patch("detect_app.services.analysis.run_inference", side_effect=inference):
                plain = service.analyze(ManualImageSource(image_path), model.identifier, 0.4)
                linked = service.analyze(ManualImageSource(image_path), model.identifier, 0.4, pcba_id=pcba["id"])
            self.assertEqual(calls[0], calls[1])
            for job_id in (plain, linked):
                log, objects = repository.get_job(job_id)
                self.assertEqual(log["image_path"].split("/")[0], job_id)
                self.assertEqual(log["annotated_image_path"], f"{job_id}/annotated.png")
                self.assertEqual(len(objects), 1)
            repository.set_job_pcba(plain, pcba["id"])
            log, _objects = repository.get_job(plain)
            self.assertTrue(service.resolve_image(log["image_path"]).is_file())
            self.assertEqual(log["image_path"].split("/")[0], plain)

    def test_annotations_are_drawn_on_the_annotated_copy_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _registry, model, repository, service = self.make_service(root)
            image_path = root / "input.png"
            Image.new("RGB", (120, 90), "white").save(image_path)
            detection = Detection(0, "ic", 0.87, 10, 10, 80, 70)
            with patch(
                "detect_app.services.analysis.run_inference",
                side_effect=lambda *_args: (Image.new("RGB", (120, 90), "white"), [detection]),
            ):
                job_id = service.analyze(ManualImageSource(image_path), model.identifier)
            log, objects = repository.get_job(job_id)
            original = Image.open(service.resolve_image(log["image_path"])).convert("RGB")
            annotated = Image.open(service.resolve_image(log["annotated_image_path"])).convert("RGB")
            crop = Image.open(service.resolve_image(objects[0]["crop_path"])).convert("RGB")
            self.assertEqual(original.getcolors(), [(120 * 90, (255, 255, 255))])
            self.assertEqual(annotated.size, original.size)
            self.assertNotEqual(annotated.getpixel((10, 40)), (255, 255, 255))
            self.assertEqual(crop.getcolors(), [(crop.width * crop.height, (255, 255, 255))])


class ModelRegistryTests(unittest.TestCase):
    def test_remove_keeps_user_source_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "user" / "mon_modele.onnx"
            source.parent.mkdir()
            shutil.copy2(REPOSITORY_ROOT / "ic_detect_best.onnx", source)
            registry = ModelRegistry(root / "models", root / "configuration")
            model = registry.register(source)
            repository = AnalysisRepository(create_session_factory(root / "database.sqlite3"))
            service = AnalysisService(repository, registry, root / "analyses")
            image_path = root / "input.png"
            Image.new("RGB", (20, 20), "white").save(image_path)
            with patch(
                "detect_app.services.analysis.run_inference",
                return_value=(Image.new("RGB", (20, 20), "white"), []),
            ):
                job_id = service.analyze(ManualImageSource(image_path), model.identifier)

            removed = registry.remove(model.identifier)

            self.assertIn(model.path.resolve(), [path.resolve() for path in removed])
            self.assertFalse(model.path.exists())
            self.assertTrue(source.is_file())
            self.assertEqual(registry.list_models(), [])
            log, _objects = repository.get_job(job_id)
            self.assertEqual(log["model_name"], model.name)
            self.assertTrue(service.resolve_image(log["annotated_image_path"]).is_file())
            self.assertTrue(service.resolve_image(log["image_path"]).is_file())

    def test_removed_bundled_model_is_not_restored_until_registered_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundled = REPOSITORY_ROOT / "ic_detect_best.onnx"
            registry = ModelRegistry(root / "models", root / "configuration")
            model = registry.ensure_bundled_model(bundled)
            registry.remove(model.identifier)
            self.assertIsNone(registry.ensure_bundled_model(bundled))
            self.assertTrue(bundled.is_file())
            registry.register(bundled)
            self.assertEqual(len(registry.list_models()), 1)


if __name__ == "__main__":
    unittest.main()
