import tempfile
import unittest
from pathlib import Path

from PIL import Image

from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.image_source import ManualImageSource
from detect_app.services.models import ModelRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class AnalysisServiceTests(unittest.TestCase):
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
