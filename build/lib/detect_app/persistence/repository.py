from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from detect_app.persistence.database import AnalysisLog, DetectedObject
from detect_app.vision.types import Detection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisRepository:
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    @contextmanager
    def _session(self):
        with self._session_factory() as session:
            yield session

    def create_job(
        self,
        job_id: str,
        image_path: str,
        image_source: str,
        model_name: str,
        model_version: str,
    ) -> None:
        with self._session_factory.begin() as session:
            session.add(
                AnalysisLog(
                    id=job_id,
                    image_path=image_path,
                    image_source=image_source,
                    model_name=model_name,
                    model_version=model_version,
                    started_at=utc_now(),
                    status="processing",
                )
            )

    def complete_job(
        self,
        job_id: str,
        detections: list[Detection],
        annotated_image_path: str,
    ) -> None:
        with self._session_factory.begin() as session:
            log = session.get(AnalysisLog, job_id)
            if log is None:
                raise ValueError(f"Analyse introuvable : {job_id}")
            for detection in detections:
                session.add(
                    DetectedObject(
                        id=str(uuid4()),
                        log_id=job_id,
                        class_id=detection.class_id,
                        class_name=detection.class_name,
                        confidence=detection.confidence,
                        x_min=detection.x_min,
                        y_min=detection.y_min,
                        x_max=detection.x_max,
                        y_max=detection.y_max,
                        crop_path=detection.crop_path,
                    )
                )
            log.annotated_image_path = annotated_image_path
            log.status = "completed"
            log.completed_at = utc_now()

    def fail_job(self, job_id: str, message: str) -> None:
        with self._session_factory.begin() as session:
            log = session.get(AnalysisLog, job_id)
            if log is not None:
                log.status = "error"
                log.error_message = message[:2000]
                log.completed_at = utc_now()

    def list_jobs(self) -> list[dict]:
        with self._session() as session:
            statement = (
                select(
                    AnalysisLog,
                    func.count(DetectedObject.id).label("object_count"),
                )
                .outerjoin(DetectedObject, DetectedObject.log_id == AnalysisLog.id)
                .group_by(AnalysisLog.id)
                .order_by(AnalysisLog.started_at.desc())
            )
            return [
                {
                    "id": log.id,
                    "started_at": log.started_at,
                    "status": log.status,
                    "model_name": log.model_name,
                    "image_path": log.image_path,
                    "annotated_image_path": log.annotated_image_path,
                    "object_count": count,
                    "error_message": log.error_message,
                }
                for log, count in session.execute(statement)
            ]

    def get_job(self, job_id: str) -> tuple[dict, list[dict]] | None:
        with self._session() as session:
            log = session.get(AnalysisLog, job_id)
            if log is None:
                return None
            objects = session.scalars(
                select(DetectedObject)
                .where(DetectedObject.log_id == job_id)
                .order_by(DetectedObject.class_id, DetectedObject.confidence.desc())
            ).all()
            return (
                {
                    "id": log.id,
                    "image_path": log.image_path,
                    "image_source": log.image_source,
                    "model_name": log.model_name,
                    "model_version": log.model_version,
                    "started_at": log.started_at,
                    "completed_at": log.completed_at,
                    "status": log.status,
                    "error_message": log.error_message,
                    "annotated_image_path": log.annotated_image_path,
                },
                [
                    {
                        "class_id": item.class_id,
                        "class_name": item.class_name,
                        "confidence": item.confidence,
                        "x_min": item.x_min,
                        "y_min": item.y_min,
                        "x_max": item.x_max,
                        "y_max": item.y_max,
                        "crop_path": item.crop_path,
                    }
                    for item in objects
                ],
            )
