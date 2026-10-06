from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from detect_app.persistence.database import AnalysisLog, Client, DetectedObject, Pcba
from detect_app.vision.types import Detection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# History filters: None means "all", UNASSIGNED means "without client/PCBA".
UNASSIGNED = ""


class ClassificationError(ValueError):
    """Explains why a client/PCBA operation is refused."""


def _clean_name(name: str, label: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ClassificationError(f"Le nom du {label} est obligatoire.")
    return cleaned


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
        pcba_id: str | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            if pcba_id is not None and session.get(Pcba, pcba_id) is None:
                raise ClassificationError("Le PCBA choisi n’existe plus.")
            session.add(
                AnalysisLog(
                    id=job_id,
                    image_path=image_path,
                    image_source=image_source,
                    model_name=model_name,
                    model_version=model_version,
                    started_at=utc_now(),
                    status="processing",
                    pcba_id=pcba_id,
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

    def recover_interrupted_jobs(self) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(AnalysisLog)
                .where(AnalysisLog.status == "processing")
                .values(
                    status="error",
                    error_message="Analyse interrompue par un arrêt de l'application ; heure de fin inconnue.",
                    completed_at=None,
                )
            )

    def list_jobs(self, client_filter: str | None = None, pcba_filter: str | None = None) -> list[dict]:
        """Jobs newest first; filters accept None (all), UNASSIGNED or an identifier."""
        with self._session() as session:
            counts = (
                select(DetectedObject.log_id, func.count(DetectedObject.id).label("object_count"))
                .group_by(DetectedObject.log_id)
                .subquery()
            )
            statement = (
                select(
                    AnalysisLog,
                    func.coalesce(counts.c.object_count, 0),
                    Pcba.name,
                    Client.id,
                    Client.name,
                )
                .outerjoin(counts, counts.c.log_id == AnalysisLog.id)
                .outerjoin(Pcba, Pcba.id == AnalysisLog.pcba_id)
                .outerjoin(Client, Client.id == Pcba.client_id)
                .order_by(AnalysisLog.started_at.desc(), AnalysisLog.id)
            )
            if client_filter == UNASSIGNED:
                statement = statement.where(Pcba.client_id.is_(None))
            elif client_filter is not None:
                statement = statement.where(Pcba.client_id == client_filter)
            if pcba_filter == UNASSIGNED:
                statement = statement.where(AnalysisLog.pcba_id.is_(None))
            elif pcba_filter is not None:
                statement = statement.where(AnalysisLog.pcba_id == pcba_filter)
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
                    "pcba_id": log.pcba_id,
                    "pcba_name": pcba_name,
                    "client_id": client_id,
                    "client_name": client_name,
                }
                for log, count, pcba_name, client_id, client_name in session.execute(statement)
            ]

    def get_job(self, job_id: str) -> tuple[dict, list[dict]] | None:
        with self._session() as session:
            log = session.get(AnalysisLog, job_id)
            if log is None:
                return None
            pcba = session.get(Pcba, log.pcba_id) if log.pcba_id else None
            client = session.get(Client, pcba.client_id) if pcba is not None and pcba.client_id else None
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
                    "pcba_id": log.pcba_id,
                    "pcba_name": pcba.name if pcba is not None else None,
                    "client_id": client.id if client is not None else None,
                    "client_name": client.name if client is not None else None,
                },
                [
                    {
                        "id": item.id,
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

    # Classification: Client -> PCBA -> jobs. Links are optional and never cascade.

    def list_clients(self) -> list[dict]:
        with self._session() as session:
            clients = session.scalars(select(Client).order_by(func.lower(Client.name), Client.id)).all()
            return [{"id": client.id, "name": client.name} for client in clients]

    def create_client(self, name: str) -> dict:
        client = {"id": str(uuid4()), "name": _clean_name(name, "client")}
        with self._session_factory.begin() as session:
            session.add(Client(**client))
        return client

    def list_pcbas(self, client_filter: str | None = None) -> list[dict]:
        """PCBAs with their client; filter None (all), UNASSIGNED (without client) or a client id."""
        with self._session() as session:
            statement = (
                select(Pcba, Client.name)
                .outerjoin(Client, Client.id == Pcba.client_id)
                .order_by(func.lower(Pcba.name), func.lower(func.coalesce(Client.name, "")), Pcba.id)
            )
            if client_filter == UNASSIGNED:
                statement = statement.where(Pcba.client_id.is_(None))
            elif client_filter is not None:
                statement = statement.where(Pcba.client_id == client_filter)
            return [
                {"id": pcba.id, "name": pcba.name, "client_id": pcba.client_id, "client_name": client_name}
                for pcba, client_name in session.execute(statement)
            ]

    def get_pcba(self, pcba_id: str) -> dict | None:
        with self._session() as session:
            pcba = session.get(Pcba, pcba_id)
            if pcba is None:
                return None
            client = session.get(Client, pcba.client_id) if pcba.client_id else None
            return {
                "id": pcba.id,
                "name": pcba.name,
                "client_id": pcba.client_id,
                "client_name": client.name if client is not None else None,
            }

    def create_pcba(self, name: str, client_id: str | None = None) -> dict:
        pcba = {"id": str(uuid4()), "name": _clean_name(name, "PCBA"), "client_id": client_id}
        with self._session_factory.begin() as session:
            if client_id is not None and session.get(Client, client_id) is None:
                raise ClassificationError("Le client choisi n’existe plus.")
            session.add(Pcba(**pcba))
        return pcba

    def count_jobs_for_pcba(self, pcba_id: str) -> int:
        with self._session() as session:
            return session.scalar(
                select(func.count(AnalysisLog.id)).where(AnalysisLog.pcba_id == pcba_id)
            ) or 0

    def set_job_pcba(self, job_id: str, pcba_id: str | None) -> None:
        """Link or unlink a job; the job folder, image and objects are untouched."""
        with self._session_factory.begin() as session:
            log = session.get(AnalysisLog, job_id)
            if log is None:
                raise ClassificationError("Analyse introuvable.")
            if log.status == "processing":
                raise ClassificationError("Le classement d’une analyse en cours ne peut pas être modifié.")
            if pcba_id is not None and session.get(Pcba, pcba_id) is None:
                raise ClassificationError("Le PCBA choisi n’existe plus.")
            log.pcba_id = pcba_id

    def set_pcba_client(self, pcba_id: str, client_id: str | None) -> int:
        """Reassign a PCBA; every job linked to it is reclassified. Returns that job count."""
        with self._session_factory.begin() as session:
            pcba = session.get(Pcba, pcba_id)
            if pcba is None:
                raise ClassificationError("PCBA introuvable.")
            if client_id is not None and session.get(Client, client_id) is None:
                raise ClassificationError("Le client choisi n’existe plus.")
            pcba.client_id = client_id
            return session.scalar(
                select(func.count(AnalysisLog.id)).where(AnalysisLog.pcba_id == pcba_id)
            ) or 0

    def delete_pcba(self, pcba_id: str) -> None:
        with self._session_factory.begin() as session:
            pcba = session.get(Pcba, pcba_id)
            if pcba is None:
                return
            linked = session.scalar(select(func.count(AnalysisLog.id)).where(AnalysisLog.pcba_id == pcba_id)) or 0
            if linked:
                raise ClassificationError(
                    f"Le PCBA « {pcba.name} » est encore lié à {linked} analyse(s). "
                    "Retirez d’abord ces liens depuis l’historique ; les analyses ne sont jamais supprimées."
                )
            session.delete(pcba)

    def delete_client(self, client_id: str) -> None:
        with self._session_factory.begin() as session:
            client = session.get(Client, client_id)
            if client is None:
                return
            linked = session.scalar(select(func.count(Pcba.id)).where(Pcba.client_id == client_id)) or 0
            if linked:
                raise ClassificationError(
                    f"Le client « {client.name} » regroupe encore {linked} PCBA. "
                    "Rattachez-les à un autre client ou retirez leur client avant de le supprimer."
                )
            session.delete(client)
