from __future__ import annotations

from datetime import datetime

from pathlib import Path

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class AnalysisLog(Base):
    __tablename__ = "analysis_logs"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'completed', 'error')", name="ck_analysis_status"),
        CheckConstraint("image_source IN ('manual', 'arducam')", name="ck_analysis_source"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    image_path: Mapped[str] = mapped_column(String, nullable=False)
    image_source: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    annotated_image_path: Mapped[str | None] = mapped_column(String, nullable=True)


class DetectedObject(Base):
    __tablename__ = "detected_objects"
    __table_args__ = (
        CheckConstraint("class_id >= 0", name="ck_detected_class_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_detected_confidence"),
        CheckConstraint("x_min >= 0 AND y_min >= 0", name="ck_detected_minimums"),
        CheckConstraint("x_max > x_min AND y_max > y_min", name="ck_detected_box_size"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    log_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("analysis_logs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    x_min: Mapped[float] = mapped_column(Float, nullable=False)
    y_min: Mapped[float] = mapped_column(Float, nullable=False)
    x_max: Mapped[float] = mapped_column(Float, nullable=False)
    y_max: Mapped[float] = mapped_column(Float, nullable=False)
    crop_path: Mapped[str | None] = mapped_column(String, nullable=True)


def create_session_factory(database_path: str | Path) -> sessionmaker:
    engine = create_engine(
        URL.create("sqlite", database=str(Path(database_path).resolve())),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
