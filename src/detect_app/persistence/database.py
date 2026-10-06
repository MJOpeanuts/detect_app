from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, Text, URL, create_engine, event
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

# Version 1: analysis_logs + detected_objects (bases créées sans PRAGMA user_version).
# Version 2: clients, pcbas et analysis_logs.pcba_id (nullable).
SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    pass


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="ck_client_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Pcba(Base):
    __tablename__ = "pcbas"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="ck_pcba_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("clients.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class AnalysisLog(Base):
    __tablename__ = "analysis_logs"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'completed', 'error')", name="ck_analysis_status"),
        CheckConstraint("image_source IN ('manual', 'arducam')", name="ck_analysis_source"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_source: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    annotated_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Declared last so new and migrated databases share the same column order.
    pcba_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("pcbas.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class DetectedObject(Base):
    __tablename__ = "detected_objects"
    __table_args__ = (
        CheckConstraint("class_id >= 0", name="ck_detected_class_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_detected_confidence"),
        CheckConstraint("x_min >= 0 AND y_min >= 0", name="ck_detected_minimums"),
        CheckConstraint("x_max > x_min AND y_max > y_min", name="ck_detected_box_size"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    log_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("analysis_logs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    x_min: Mapped[float] = mapped_column(Float, nullable=False)
    y_min: Mapped[float] = mapped_column(Float, nullable=False)
    x_max: Mapped[float] = mapped_column(Float, nullable=False)
    y_max: Mapped[float] = mapped_column(Float, nullable=False)
    crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)


def _ddl(element) -> str:
    return str(element.compile(dialect=sqlite_dialect.dialect())).strip()


def backup_database(database_path: Path, version: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = database_path.with_name(f"{database_path.name}.backup-v{version}-{stamp}")
    source = sqlite3.connect(database_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def migrate_schema(database_path: str | Path) -> Path | None:
    """Upgrade an existing database in place; returns the backup path when one was made."""
    database_path = Path(database_path).resolve()
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"La base locale utilise le schéma {version}, plus récent que celui pris en charge "
                f"({SCHEMA_VERSION}). Mettez detect_app à jour."
            )
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "analysis_logs" not in tables:
            return None
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_logs)")}
        if "pcba_id" in columns and version == SCHEMA_VERSION:
            return None
        backup = backup_database(database_path, version or 1)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in (Client.__table__, Pcba.__table__):
                connection.execute(_ddl(CreateTable(table, if_not_exists=True)))
                for index in table.indexes:
                    connection.execute(_ddl(CreateIndex(index, if_not_exists=True)))
            if "pcba_id" not in columns:
                connection.execute(
                    "ALTER TABLE analysis_logs ADD COLUMN pcba_id TEXT "
                    "REFERENCES pcbas (id) ON DELETE RESTRICT"
                )
            for index in AnalysisLog.__table__.indexes:
                connection.execute(_ddl(CreateIndex(index, if_not_exists=True)))
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return backup
    finally:
        connection.close()


def create_session_factory(database_path: str | Path) -> sessionmaker:
    migrate_schema(database_path)
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
    with engine.begin() as connection:
        if connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 0:
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return sessionmaker(bind=engine, expire_on_commit=False)
