import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.vision.types import Detection


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nuts_vision.sqlite3"
        self.sessions = create_session_factory(self.database_path)
        self.repository = AnalysisRepository(self.sessions)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_database_has_exactly_two_business_tables_and_text_columns(self):
        inspector = inspect(self.sessions.kw["bind"])
        self.assertEqual(set(inspector.get_table_names()), {"analysis_logs", "detected_objects"})
        self.assertEqual(
            [column["name"] for column in inspector.get_columns("analysis_logs")],
            [
                "id",
                "image_path",
                "image_source",
                "model_name",
                "model_version",
                "started_at",
                "completed_at",
                "status",
                "error_message",
                "annotated_image_path",
            ],
        )
        self.assertTrue(all(str(column["type"]) == "TEXT" for column in inspector.get_columns("analysis_logs")))
        with self.sessions() as session:
            self.assertEqual(session.execute(text("PRAGMA foreign_keys")).scalar_one(), 1)

    def test_analysis_and_detection_round_trip_and_restrict_delete(self):
        job_id = str(uuid4())
        self.repository.create_job(job_id, "job/original.png", "manual", "ic_detect_best", "a" * 64)
        detection = Detection(0, "four_side", 0.9, 1.0, 2.0, 20.0, 25.0, "job/crops/0001.png")
        self.repository.complete_job(job_id, [detection], "job/annotated.png")
        log, objects = self.repository.get_job(job_id)
        self.assertEqual(log["status"], "completed")
        self.assertIsNotNone(log["completed_at"])
        self.assertEqual(objects[0]["class_name"], "four_side")
        self.assertEqual(self.repository.list_jobs()[0]["object_count"], 1)
        with self.assertRaises(IntegrityError):
            with self.sessions.begin() as session:
                from detect_app.persistence.database import AnalysisLog

                session.delete(session.get(AnalysisLog, job_id))

    def test_database_rejects_invalid_confidence(self):
        job_id = str(uuid4())
        self.repository.create_job(job_id, "job/original.png", "manual", "model", "b" * 64)
        invalid = Detection(0, "part", 1.1, 0.0, 0.0, 5.0, 5.0)
        with self.assertRaises(IntegrityError):
            self.repository.complete_job(job_id, [invalid], "job/annotated.png")
        log, objects = self.repository.get_job(job_id)
        self.assertEqual(log["status"], "processing")
        self.assertEqual(objects, [])


if __name__ == "__main__":
    unittest.main()
