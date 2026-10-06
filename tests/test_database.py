import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from detect_app.persistence.database import SCHEMA_VERSION, create_session_factory
from detect_app.persistence.repository import UNASSIGNED, AnalysisRepository, ClassificationError
from detect_app.vision.types import Detection


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "detect_app.sqlite3"
        self.sessions = create_session_factory(self.database_path)
        self.repository = AnalysisRepository(self.sessions)

    def tearDown(self):
        self.sessions.kw["bind"].dispose()
        self.temporary_directory.cleanup()

    def test_database_has_business_tables_and_text_columns(self):
        inspector = inspect(self.sessions.kw["bind"])
        self.assertEqual(
            set(inspector.get_table_names()),
            {"analysis_logs", "detected_objects", "clients", "pcbas"},
        )
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
                "pcba_id",
            ],
        )
        self.assertEqual([column["name"] for column in inspector.get_columns("clients")], ["id", "name"])
        self.assertEqual(
            [column["name"] for column in inspector.get_columns("pcbas")], ["id", "name", "client_id"]
        )
        self.assertTrue(next(c for c in inspector.get_columns("analysis_logs") if c["name"] == "pcba_id")["nullable"])
        self.assertTrue(next(c for c in inspector.get_columns("pcbas") if c["name"] == "client_id")["nullable"])
        self.assertIn(
            {"referred_table": "pcbas", "constrained_columns": ["pcba_id"]},
            [
                {"referred_table": fk["referred_table"], "constrained_columns": fk["constrained_columns"]}
                for fk in inspector.get_foreign_keys("analysis_logs")
            ],
        )
        self.assertEqual(inspector.get_foreign_keys("pcbas")[0]["referred_table"], "clients")
        self.assertIn(["pcba_id"], [index["column_names"] for index in inspector.get_indexes("analysis_logs")])
        self.assertIn(["client_id"], [index["column_names"] for index in inspector.get_indexes("pcbas")])
        with self.sessions() as session:
            self.assertEqual(session.execute(text("PRAGMA user_version")).scalar_one(), SCHEMA_VERSION)
        self.assertEqual(
            [column["name"] for column in inspector.get_columns("detected_objects")],
            [
                "id",
                "log_id",
                "class_id",
                "class_name",
                "confidence",
                "x_min",
                "y_min",
                "x_max",
                "y_max",
                "crop_path",
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

    def test_recovery_only_changes_processing_jobs_and_preserves_files(self):
        image = Path(self.temporary_directory.name) / "original.png"
        image.write_bytes(b"diagnostic image")
        for job_id in ("pending-a", "pending-b", "completed", "failed"):
            self.repository.create_job(job_id, str(image), "manual", "model", "b" * 64)
        self.repository.complete_job("completed", [], "annotated.png")
        self.repository.fail_job("failed", "Original error")
        completed = self.repository.get_job("completed")
        failed = self.repository.get_job("failed")
        pending = self.repository.get_job("pending-a")[0]

        self.repository.recover_interrupted_jobs()
        for job_id in ("pending-a", "pending-b"):
            log, objects = self.repository.get_job(job_id)
            self.assertEqual(log["status"], "error")
            self.assertIn("interrompue", log["error_message"])
            self.assertIsNone(log["completed_at"])
            self.assertEqual(log["image_path"], str(image))
            self.assertEqual(objects, [])
        self.assertEqual(self.repository.get_job("pending-a")[0]["started_at"], pending["started_at"])
        self.assertEqual(self.repository.get_job("completed"), completed)
        self.assertEqual(self.repository.get_job("failed"), failed)
        self.assertEqual(image.read_bytes(), b"diagnostic image")
        recovered = self.repository.get_job("pending-a")
        self.repository.recover_interrupted_jobs()
        self.assertEqual(self.repository.get_job("pending-a"), recovered)

    def test_recovery_rolls_back_its_transaction_on_error(self):
        self.repository.create_job("pending", "original.png", "manual", "model", "b" * 64)
        with self.sessions.begin() as session:
            session.execute(text(
                "CREATE TRIGGER reject_recovery BEFORE UPDATE ON analysis_logs "
                "BEGIN SELECT RAISE(ABORT, 'recovery failed'); END"
            ))
        with self.assertRaises(IntegrityError):
            self.repository.recover_interrupted_jobs()
        log, _ = self.repository.get_job("pending")
        self.assertEqual(log["status"], "processing")
        self.assertIsNone(log["error_message"])



class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.sessions = create_session_factory(Path(self.temporary_directory.name) / "detect_app.sqlite3")
        self.repository = AnalysisRepository(self.sessions)

    def tearDown(self):
        self.sessions.kw["bind"].dispose()
        self.temporary_directory.cleanup()

    def job(self, job_id, pcba_id=None, complete=True):
        self.repository.create_job(job_id, f"{job_id}/original.png", "manual", "model", "a" * 64, pcba_id)
        if complete:
            self.repository.complete_job(job_id, [], f"{job_id}/annotated.png")

    def test_job_without_classification_is_unchanged(self):
        self.job("plain")
        log, _ = self.repository.get_job("plain")
        self.assertIsNone(log["pcba_id"])
        self.assertIsNone(log["client_name"])
        listed = self.repository.list_jobs()[0]
        self.assertIsNone(listed["pcba_name"])

    def test_pcba_without_client_and_hierarchy(self):
        client = self.repository.create_client("  Acme  ")
        self.assertEqual(client["name"], "Acme")
        board_a = self.repository.create_pcba("Carte", client["id"])
        board_b = self.repository.create_pcba("Alim", client["id"])
        orphan = self.repository.create_pcba("Carte")
        other = self.repository.create_client("Globex")
        same_name = self.repository.create_pcba("Carte", other["id"])
        self.assertNotEqual(board_a["id"], same_name["id"])
        self.assertIsNone(self.repository.get_pcba(orphan["id"])["client_id"])
        self.assertEqual(
            {pcba["id"] for pcba in self.repository.list_pcbas(client["id"])}, {board_a["id"], board_b["id"]}
        )
        self.assertEqual([pcba["id"] for pcba in self.repository.list_pcbas(UNASSIGNED)], [orphan["id"]])
        self.assertEqual(len(self.repository.list_pcbas()), 4)
        for job_id in ("j1", "j2"):
            self.job(job_id, board_a["id"])
        self.job("j3", orphan["id"])
        self.job("j4")
        self.assertEqual(self.repository.count_jobs_for_pcba(board_a["id"]), 2)
        log, _ = self.repository.get_job("j1")
        self.assertEqual((log["client_name"], log["pcba_name"]), ("Acme", "Carte"))
        log, _ = self.repository.get_job("j3")
        self.assertEqual((log["client_name"], log["pcba_name"]), (None, "Carte"))
        with self.assertRaises(ClassificationError):
            self.repository.create_client("   ")
        with self.assertRaises(ClassificationError):
            self.repository.create_pcba("", None)
        with self.assertRaises(IntegrityError):
            with self.sessions.begin() as session:
                session.execute(text("INSERT INTO clients (id, name) VALUES ('x', '  ')"))

    def test_history_filters(self):
        client = self.repository.create_client("Acme")
        linked = self.repository.create_pcba("Carte", client["id"])
        orphan = self.repository.create_pcba("Libre")
        self.job("linked", linked["id"])
        self.job("orphan", orphan["id"])
        self.job("none")

        def ids(**filters):
            return {job["id"] for job in self.repository.list_jobs(**filters)}

        self.assertEqual(ids(), {"linked", "orphan", "none"})
        self.assertEqual(ids(client_filter=client["id"]), {"linked"})
        self.assertEqual(ids(client_filter=UNASSIGNED), {"orphan", "none"})
        self.assertEqual(ids(client_filter=UNASSIGNED, pcba_filter=UNASSIGNED), {"none"})
        self.assertEqual(ids(pcba_filter=UNASSIGNED), {"none"})
        self.assertEqual(ids(pcba_filter=orphan["id"]), {"orphan"})
        self.assertEqual(ids(client_filter=client["id"], pcba_filter=orphan["id"]), set())

    def test_history_order_is_chronological_inside_filter(self):
        board = self.repository.create_pcba("Carte")
        for job_id in ("first", "second", "third"):
            self.job(job_id, board["id"])
        self.assertEqual(
            [job["id"] for job in self.repository.list_jobs(pcba_filter=board["id"])],
            ["third", "second", "first"],
        )

    def test_failed_job_keeps_classification(self):
        board = self.repository.create_pcba("Carte")
        self.job("failed", board["id"], complete=False)
        self.repository.fail_job("failed", "boom")
        log, _ = self.repository.get_job("failed")
        self.assertEqual((log["status"], log["pcba_id"]), ("error", board["id"]))

    def test_reclassify_after_analysis_and_processing_is_locked(self):
        board = self.repository.create_pcba("Carte")
        self.job("done")
        self.repository.set_job_pcba("done", board["id"])
        self.assertEqual(self.repository.get_job("done")[0]["pcba_id"], board["id"])
        self.repository.set_job_pcba("done", None)
        log, _ = self.repository.get_job("done")
        self.assertIsNone(log["pcba_id"])
        self.assertEqual(log["image_path"], "done/original.png")
        self.job("running", complete=False)
        with self.assertRaises(ClassificationError):
            self.repository.set_job_pcba("running", board["id"])
        with self.assertRaises(ClassificationError):
            self.repository.set_job_pcba("done", "missing")

    def test_changing_pcba_client_reclassifies_all_its_jobs(self):
        first = self.repository.create_client("Acme")
        second = self.repository.create_client("Globex")
        board = self.repository.create_pcba("Carte", first["id"])
        self.job("a", board["id"])
        self.job("b", board["id"])
        self.assertEqual(self.repository.set_pcba_client(board["id"], second["id"]), 2)
        self.assertEqual({job["client_name"] for job in self.repository.list_jobs()}, {"Globex"})
        self.repository.set_pcba_client(board["id"], None)
        self.assertEqual({job["client_name"] for job in self.repository.list_jobs()}, {None})

    def test_deletions_are_blocked_without_cascade(self):
        client = self.repository.create_client("Acme")
        board = self.repository.create_pcba("Carte", client["id"])
        self.job("kept", board["id"])
        with self.assertRaisesRegex(ClassificationError, "encore lié"):
            self.repository.delete_pcba(board["id"])
        with self.assertRaisesRegex(ClassificationError, "regroupe encore"):
            self.repository.delete_client(client["id"])
        for statement in ("DELETE FROM pcbas", "DELETE FROM clients"):
            with self.assertRaises(IntegrityError):
                with self.sessions.begin() as session:
                    session.execute(text(statement))
        self.assertEqual(self.repository.get_job("kept")[0]["pcba_id"], board["id"])
        self.repository.set_job_pcba("kept", None)
        self.repository.delete_pcba(board["id"])
        self.repository.delete_client(client["id"])
        self.assertIsNotNone(self.repository.get_job("kept"))
        self.assertEqual(self.repository.list_clients(), [])


class SchemaMigrationTests(unittest.TestCase):
    VERSION_1_SCHEMA = (
        "CREATE TABLE analysis_logs (id TEXT NOT NULL, image_path TEXT NOT NULL, image_source TEXT NOT NULL, "
        "model_name TEXT NOT NULL, model_version TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, "
        "status TEXT NOT NULL, error_message TEXT, annotated_image_path TEXT, PRIMARY KEY (id), "
        "CONSTRAINT ck_analysis_status CHECK (status IN ('processing', 'completed', 'error')), "
        "CONSTRAINT ck_analysis_source CHECK (image_source IN ('manual', 'arducam')))",
        "CREATE TABLE detected_objects (id TEXT NOT NULL, log_id TEXT NOT NULL, class_id INTEGER NOT NULL, "
        "class_name TEXT NOT NULL, confidence FLOAT NOT NULL, x_min FLOAT NOT NULL, y_min FLOAT NOT NULL, "
        "x_max FLOAT NOT NULL, y_max FLOAT NOT NULL, crop_path TEXT, PRIMARY KEY (id), "
        "FOREIGN KEY(log_id) REFERENCES analysis_logs (id) ON DELETE RESTRICT)",
        "CREATE INDEX ix_detected_objects_log_id ON detected_objects (log_id)",
        "INSERT INTO analysis_logs VALUES ('old', 'old/original.png', 'manual', 'model', 'sha', "
        "'2024-01-01T00:00:00+00:00', '2024-01-01T00:00:01+00:00', 'completed', NULL, 'old/annotated.png')",
        "INSERT INTO detected_objects VALUES ('o1', 'old', 0, 'part', 0.9, 1, 2, 10, 12, 'old/crops/0001.png')",
    )

    def test_version_1_database_is_upgraded_without_losing_analyses(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "detect_app.sqlite3"
            connection = sqlite3.connect(path)
            for statement in self.VERSION_1_SCHEMA:
                connection.execute(statement)
            connection.commit()
            connection.close()

            sessions = create_session_factory(path)
            try:
                repository = AnalysisRepository(sessions)
                log, objects = repository.get_job("old")
                self.assertEqual(log["status"], "completed")
                self.assertIsNone(log["pcba_id"])
                self.assertEqual(log["annotated_image_path"], "old/annotated.png")
                self.assertEqual(objects[0]["class_name"], "part")
                backups = list(Path(temporary).glob("detect_app.sqlite3.backup-v1-*"))
                self.assertEqual(len(backups), 1)
                backup = sqlite3.connect(backups[0])
                self.assertEqual(backup.execute("SELECT id FROM analysis_logs").fetchall(), [("old",)])
                self.assertNotIn("pcba_id", [row[1] for row in backup.execute("PRAGMA table_info(analysis_logs)")])
                backup.close()
                board = repository.create_pcba("Carte")
                repository.set_job_pcba("old", board["id"])
                with self.assertRaises(IntegrityError):
                    with sessions.begin() as session:
                        session.execute(text("UPDATE analysis_logs SET pcba_id = 'missing'"))
                with self.assertRaises(IntegrityError):
                    with sessions.begin() as session:
                        session.execute(text("DELETE FROM pcbas"))
                inspector = inspect(sessions.kw["bind"])
                self.assertIn(["pcba_id"], [index["column_names"] for index in inspector.get_indexes("analysis_logs")])
                with sessions() as session:
                    self.assertEqual(session.execute(text("PRAGMA user_version")).scalar_one(), SCHEMA_VERSION)
            finally:
                sessions.kw["bind"].dispose()

            reopened = create_session_factory(path)
            try:
                self.assertEqual(AnalysisRepository(reopened).get_job("old")[0]["pcba_name"], "Carte")
                self.assertEqual(len(list(Path(temporary).glob("detect_app.sqlite3.backup-*"))), 1)
            finally:
                reopened.kw["bind"].dispose()

    def test_newer_schema_is_refused_without_changes(self):
        import sqlite3

        from detect_app.persistence.database import SchemaVersionError

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "detect_app.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(self.VERSION_1_SCHEMA[0])
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            connection.commit()
            connection.close()
            with self.assertRaises(SchemaVersionError):
                create_session_factory(path)


if __name__ == "__main__":
    unittest.main()
