from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.analysis.analyzer import AnalysisResult
from app.parser.inspection_record import InspectionRecord

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Inspection(Base):

    __tablename__ = "inspection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Unique per physical inspection (filename id + arrival time).
    uid: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)

    # ID from the IV4 filename / TXT. NOT unique: IV4 counters can reset.
    inspection_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # SHA-256 of the files: the real duplicate protection.
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    timestamp: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    machine_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    result: Mapped[str | None] = mapped_column(String(50), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    analysis_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    analysis_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    image_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stored as UTC
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DatabaseRepository:

    def __init__(self, database_path: str | Path):

        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(
            f"sqlite:///{self.database_path.resolve()}",
            connect_args={"timeout": 30},
        )

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")       # readers (dashboard) don't block writer
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        self._migrate_legacy()
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))

    # --------------------------------------------------------
    # Migration from the v1 schema (inspection_id UNIQUE, no uid)
    # --------------------------------------------------------

    def _migrate_legacy(self) -> None:
        insp = inspect(self.engine)
        if "inspection" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("inspection")}
        if "uid" in cols:
            return

        legacy = f"inspection_v1_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        log.warning("[DB] migrating legacy schema -> v%s (backup table %s)", SCHEMA_VERSION, legacy)
        with self.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE inspection RENAME TO {legacy}"))
            # Index names are global in SQLite; drop the old ones so create_all can recreate them.
            for idx in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"),
                {"t": legacy},
            ).scalars().all():
                conn.execute(text(f'DROP INDEX IF EXISTS "{idx}"'))
            Base.metadata.create_all(conn)
            conn.execute(text(f"""
                INSERT INTO inspection (
                    uid, inspection_id, timestamp, machine_id, camera_id, result, score,
                    width, height, defect_count, inspection_time_ms, confidence,
                    analysis_status, analysis_reason, created_at, updated_at)
                SELECT inspection_id || '__legacy', inspection_id, timestamp, machine_id,
                    camera_id, result, score, width, height, defect_count,
                    inspection_time_ms, confidence, analysis_status, analysis_reason,
                    created_at, created_at
                FROM {legacy}
            """))

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    def save_or_update_inspection(
        self,
        record: InspectionRecord,
        analysis: AnalysisResult,
        uid: str | None = None,
        content_hash: str | None = None,
        image_file: str | None = None,
        folder: str | None = None,
    ) -> tuple[Inspection, str]:
        """
        Upsert keyed by ``uid`` (safe to retry after a crash).

        Returns (row, action) where action is CREATED, UPDATED or DUPLICATE.
        DUPLICATE = the exact same files were already stored under another uid.
        """
        uid = uid or record.inspection_id

        values = dict(
            inspection_id=record.inspection_id,
            timestamp=record.timestamp,
            machine_id=record.machine_id,
            camera_id=record.camera_id,
            result=record.result,
            score=record.score,
            width=record.width,
            height=record.height,
            defect_count=record.defect_count,
            inspection_time_ms=record.inspection_time_ms,
            confidence=record.confidence,
            analysis_status=analysis.status,
            analysis_reason="; ".join(analysis.reasons or [])[:1000],
            image_file=image_file,
            folder=folder,
            raw_data=json.dumps(record.raw, ensure_ascii=False, default=str),
            parse_warnings="; ".join(record.warnings) or None,
        )

        with Session(self.engine, expire_on_commit=False) as session:

            if content_hash:
                dup = session.scalar(
                    select(Inspection).where(
                        Inspection.content_hash == content_hash,
                        Inspection.uid != uid,
                    )
                )
                if dup is not None:
                    return dup, "DUPLICATE"

            existing = session.scalar(select(Inspection).where(Inspection.uid == uid))

            if existing is not None:
                for k, v in values.items():
                    setattr(existing, k, v)
                if content_hash:
                    existing.content_hash = content_hash
                session.commit()
                return existing, "UPDATED"

            row = Inspection(uid=uid, content_hash=content_hash, **values)
            session.add(row)
            session.commit()
            return row, "CREATED"

    # Backwards-compatible name
    def save_inspection(self, record: InspectionRecord, analysis: AnalysisResult) -> Inspection:
        return self.save_or_update_inspection(record, analysis)[0]

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    def get_by_uid(self, uid: str) -> Inspection | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.scalar(select(Inspection).where(Inspection.uid == uid))

    def get_by_inspection_id(self, inspection_id: str) -> Inspection | None:
        """Latest inspection with this (possibly reused) IV4 id."""
        with Session(self.engine, expire_on_commit=False) as session:
            return session.scalar(
                select(Inspection)
                .where(Inspection.inspection_id == inspection_id)
                .order_by(Inspection.id.desc())
            )

    def list_by_inspection_id(self, inspection_id: str) -> list[Inspection]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(Inspection)
                    .where(Inspection.inspection_id == inspection_id)
                    .order_by(Inspection.id)
                )
            )

    def count(self) -> int:
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(Inspection)) or 0

    def count_by_status(self) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(Inspection.analysis_status, func.count()).group_by(Inspection.analysis_status)
            ).all()
            return {str(s): n for s, n in rows}

    def dispose(self) -> None:
        self.engine.dispose()
