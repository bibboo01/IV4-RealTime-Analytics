from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    DateTime,
    PrimaryKeyConstraint,
    delete,
    update,
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

SCHEMA_VERSION = 4


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

    # KEYENCE IV4
    program_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_no: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_format: Mapped[str | None] = mapped_column(String(20), nullable=True)

    analysis_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    analysis_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    image_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Online storage
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    upload_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Stored as UTC
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class HourlyStats(Base):
    """
    One row per (hour, program, sensor). Updated in the same transaction as
    each new inspection, so dashboards never have to scan millions of rows.

    hour = sensor local time 'YYYY-MM-DD HH' (from 'Time and Date').
    trigger_min/max: sensor counter range seen in this hour; when the counter
    did not reset, (trigger_max - trigger_min + 1) - total = files missing.
    """

    __tablename__ = "hourly_stats"
    __table_args__ = (PrimaryKeyConstraint("hour", "program_no", "sensor_id"),)

    hour: Mapped[str] = mapped_column(String(13))
    program_no: Mapped[int] = mapped_column(Integer, default=-1)
    sensor_id: Mapped[str] = mapped_column(String(100), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    time_ms_sum: Mapped[int] = mapped_column(Integer, default=0)
    time_ms_count: Mapped[int] = mapped_column(Integer, default=0)
    time_ms_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_resets: Mapped[int] = mapped_column(Integer, default=0)
    last_trigger: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_min: Mapped[float | None] = mapped_column(Float, nullable=True)


class HourlyToolStats(Base):
    """Per-tool counts per hour: which tool causes NG, and its value trend."""

    __tablename__ = "hourly_tool_stats"
    __table_args__ = (PrimaryKeyConstraint("hour", "program_no", "sensor_id", "tool_no"),)

    hour: Mapped[str] = mapped_column(String(13))
    program_no: Mapped[int] = mapped_column(Integer, default=-1)
    sensor_id: Mapped[str] = mapped_column(String(100), default="")
    tool_no: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    ng_count: Mapped[int] = mapped_column(Integer, default=0)
    value_sum: Mapped[float] = mapped_column(Float, default=0.0)
    value_count: Mapped[int] = mapped_column(Integer, default=0)
    value_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    ok_value_min: Mapped[float | None] = mapped_column(Float, nullable=True)


def _nmin(col: str) -> str:
    return (f"CASE WHEN excluded.{col} IS NULL THEN {col} WHEN {col} IS NULL THEN excluded.{col} "
            f"ELSE MIN({col}, excluded.{col}) END")


def _nmax(col: str) -> str:
    return (f"CASE WHEN excluded.{col} IS NULL THEN {col} WHEN {col} IS NULL THEN excluded.{col} "
            f"ELSE MAX({col}, excluded.{col}) END")


_HOURLY_UPSERT = text(f"""
INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,
    time_ms_sum, time_ms_count, time_ms_max, trigger_min, trigger_max, trigger_resets, last_trigger, score_min)
VALUES (:hour, :program_no, :sensor_id, 1, :p, :f, :u, :tms, :tmc, :tmax, :trig, :trig, 0, :trig, :score)
ON CONFLICT (hour, program_no, sensor_id) DO UPDATE SET
    total = total + 1,
    pass_count = pass_count + excluded.pass_count,
    fail_count = fail_count + excluded.fail_count,
    unknown_count = unknown_count + excluded.unknown_count,
    time_ms_sum = time_ms_sum + excluded.time_ms_sum,
    time_ms_count = time_ms_count + excluded.time_ms_count,
    time_ms_max = {_nmax("time_ms_max")},
    trigger_min = {_nmin("trigger_min")},
    trigger_max = {_nmax("trigger_max")},
    trigger_resets = trigger_resets + CASE
        WHEN excluded.last_trigger IS NOT NULL AND last_trigger IS NOT NULL
             AND excluded.last_trigger < last_trigger - 1000 THEN 1 ELSE 0 END,
    last_trigger = COALESCE(excluded.last_trigger, last_trigger),
    score_min = {_nmin("score_min")}
""")

_TOOL_UPSERT = text(f"""
INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count,
    value_sum, value_count, value_min, ok_value_min)
VALUES (:hour, :program_no, :sensor_id, :tool_no, :tool_name, 1, :ng, :vsum, :vcnt, :vmin, :okmin)
ON CONFLICT (hour, program_no, sensor_id, tool_no) DO UPDATE SET
    tool_name = excluded.tool_name,
    count = count + 1,
    ng_count = ng_count + excluded.ng_count,
    value_sum = value_sum + excluded.value_sum,
    value_count = value_count + excluded.value_count,
    value_min = {_nmin("value_min")},
    ok_value_min = {_nmin("ok_value_min")}
""")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _hour_key(record: InspectionRecord) -> str:
    ts = record.timestamp or ""
    if len(ts) >= 13 and ts[4] == "-" and ts[10] == " ":
        return ts[:13]
    return datetime.now().strftime("%Y-%m-%d %H")


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
        self._add_missing_columns()
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

    def _add_missing_columns(self) -> None:
        """v2 -> v3+: add new nullable columns in place (SQLite ALTER TABLE ADD COLUMN)."""
        existing = {c["name"] for c in inspect(self.engine).get_columns("inspection")}
        table = Base.metadata.tables["inspection"]
        with self.engine.begin() as conn:
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=self.engine.dialect)
                conn.execute(text(f'ALTER TABLE inspection ADD COLUMN "{col.name}" {coltype}'))
                log.warning("[DB] added column %s", col.name)
            for idx in table.indexes:
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "{idx.name}" ON inspection '
                    f'({", ".join(c.name for c in idx.columns)})'
                ))

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
            # IV4 rows: everything is already in typed columns + tools_json;
            # keep raw only when the sensor sent lines we do not map.
            raw_data=(
                json.dumps(record.raw, ensure_ascii=False, default=str)
                if record.source_format != "iv4" or record.raw.get("extra")
                else None
            ),
            parse_warnings="; ".join(record.warnings) or None,
            program_no=record.program_no,
            trigger_no=record.trigger_no,
            tools_json=json.dumps(record.tools, ensure_ascii=False) if record.tools else None,
            source_format=record.source_format,
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
            self._update_hourly(session, record, analysis)
            session.commit()
            return row, "CREATED"

    @staticmethod
    def _update_hourly(session: Session, record: InspectionRecord, analysis: AnalysisResult) -> None:
        key = dict(
            hour=_hour_key(record),
            program_no=record.program_no if record.program_no is not None else -1,
            sensor_id=record.camera_id or "",
        )
        st = analysis.status
        tms = record.inspection_time_ms
        session.execute(_HOURLY_UPSERT, dict(
            key,
            p=int(st == "PASS"), f=int(st == "FAIL"), u=int(st not in ("PASS", "FAIL")),
            tms=tms or 0, tmc=int(tms is not None), tmax=tms,
            trig=record.trigger_no, score=record.score,
        ))
        for t in record.tools or []:
            v = _num(t.get("value"))
            ng = (t.get("status") or "").upper() == "NG"
            session.execute(_TOOL_UPSERT, dict(
                key,
                tool_no=t.get("no", 0), tool_name=t.get("name"), ng=int(ng),
                vsum=v or 0.0, vcnt=int(v is not None), vmin=v,
                okmin=v if not ng else None,
            ))

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

    # --------------------------------------------------------
    # Archive / upload / retention helpers
    # --------------------------------------------------------

    def set_folder(self, uid: str, folder: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(Inspection).where(Inspection.uid == uid).values(folder=folder))

    def pending_uploads(self, statuses: frozenset[str] | None, limit: int) -> list[tuple[str, str]]:
        q = select(Inspection.uid, Inspection.folder).where(
            Inspection.uploaded_at.is_(None), Inspection.folder.is_not(None)
        )
        if statuses is not None:
            q = q.where(Inspection.analysis_status.in_(sorted(statuses)))
        with Session(self.engine) as session:
            return [tuple(r) for r in session.execute(q.order_by(Inspection.id).limit(limit))]

    def count_pending_uploads(self, statuses: frozenset[str] | None) -> int:
        q = select(func.count()).select_from(Inspection).where(
            Inspection.uploaded_at.is_(None), Inspection.folder.is_not(None)
        )
        if statuses is not None:
            q = q.where(Inspection.analysis_status.in_(sorted(statuses)))
        with Session(self.engine) as session:
            return session.scalar(q) or 0

    def mark_uploaded(self, uid: str, ref: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(Inspection).where(Inspection.uid == uid).values(uploaded_at=_utcnow(), upload_ref=ref)
            )

    def delete_rows_before(self, cutoff: datetime, batch: int = 5000) -> int:
        """Delete per-inspection rows older than cutoff (UTC) in small batches."""
        total = 0
        while True:
            with self.engine.begin() as conn:
                ids = conn.execute(
                    select(Inspection.id).where(Inspection.created_at < cutoff).limit(batch)
                ).scalars().all()
                if not ids:
                    return total
                conn.execute(delete(Inspection).where(Inspection.id.in_(ids)))
            total += len(ids)

    def dispose(self) -> None:
        self.engine.dispose()
