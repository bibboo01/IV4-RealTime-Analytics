from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    DateTime,
    insert,
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
VALUES (:hour, :program_no, :sensor_id, :total, :p, :f, :u, :tms, :tmc, :tmax, :tmin_trig, :tmax_trig,
    :resets, :last_trig, :score)
ON CONFLICT (hour, program_no, sensor_id) DO UPDATE SET
    total = total + excluded.total,
    pass_count = pass_count + excluded.pass_count,
    fail_count = fail_count + excluded.fail_count,
    unknown_count = unknown_count + excluded.unknown_count,
    time_ms_sum = time_ms_sum + excluded.time_ms_sum,
    time_ms_count = time_ms_count + excluded.time_ms_count,
    time_ms_max = {_nmax("time_ms_max")},
    trigger_min = {_nmin("trigger_min")},
    trigger_max = {_nmax("trigger_max")},
    trigger_resets = trigger_resets + excluded.trigger_resets + CASE
        WHEN :first_trig IS NOT NULL AND last_trigger IS NOT NULL
             AND :first_trig < last_trigger - 1000 THEN 1 ELSE 0 END,
    last_trigger = COALESCE(excluded.last_trigger, last_trigger),
    score_min = {_nmin("score_min")}
""")

_TOOL_UPSERT = text(f"""
INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count,
    value_sum, value_count, value_min, ok_value_min)
VALUES (:hour, :program_no, :sensor_id, :tool_no, :tool_name, :count, :ng, :vsum, :vcnt, :vmin, :okmin)
ON CONFLICT (hour, program_no, sensor_id, tool_no) DO UPDATE SET
    tool_name = excluded.tool_name,
    count = count + excluded.count,
    ng_count = ng_count + excluded.ng_count,
    value_sum = value_sum + excluded.value_sum,
    value_count = value_count + excluded.value_count,
    value_min = {_nmin("value_min")},
    ok_value_min = {_nmin("ok_value_min")}
""")


def _mn(a, b):
    return b if a is None else a if b is None else min(a, b)


def _mx(a, b):
    return b if a is None else a if b is None else max(a, b)


def aggregate_hourly(pairs) -> tuple[list[dict], list[dict]]:
    """
    Pre-aggregate (record, analysis) pairs per hour/program/sensor so a batch
    of 200 inspections becomes a handful of upserts instead of ~600.
    Order matters for counter-reset detection, so pairs are processed in order.
    """
    hours: dict[tuple, dict] = {}
    tools: dict[tuple, dict] = {}
    for record, analysis in pairs:
        key = (_hour_key(record), record.program_no if record.program_no is not None else -1,
               record.camera_id or "")
        h = hours.get(key)
        if h is None:
            h = hours[key] = dict(hour=key[0], program_no=key[1], sensor_id=key[2], total=0, p=0, f=0, u=0,
                                  tms=0, tmc=0, tmax=None, tmin_trig=None, tmax_trig=None, resets=0,
                                  first_trig=None, last_trig=None, score=None)
        st = analysis.status
        h["total"] += 1
        h["p"] += st == "PASS"
        h["f"] += st == "FAIL"
        h["u"] += st not in ("PASS", "FAIL")
        tms = record.inspection_time_ms
        if tms is not None:
            h["tms"] += tms
            h["tmc"] += 1
            h["tmax"] = _mx(h["tmax"], tms)
        trig = record.trigger_no
        if trig is not None:
            if h["first_trig"] is None:
                h["first_trig"] = trig
            elif h["last_trig"] is not None and trig < h["last_trig"] - 1000:
                h["resets"] += 1
            h["last_trig"] = trig
            h["tmin_trig"] = _mn(h["tmin_trig"], trig)
            h["tmax_trig"] = _mx(h["tmax_trig"], trig)
        h["score"] = _mn(h["score"], record.score)

        for t in record.tools or []:
            tk = key + (t.get("no", 0),)
            v = _num(t.get("value"))
            ng = (t.get("status") or "").upper() == "NG"
            g = tools.get(tk)
            if g is None:
                g = tools[tk] = dict(hour=key[0], program_no=key[1], sensor_id=key[2], tool_no=tk[3],
                                     tool_name=None, count=0, ng=0, vsum=0.0, vcnt=0, vmin=None, okmin=None)
            g["tool_name"] = t.get("name")
            g["count"] += 1
            g["ng"] += ng
            if v is not None:
                g["vsum"] += v
                g["vcnt"] += 1
                g["vmin"] = _mn(g["vmin"], v)
                if not ng:
                    g["okmin"] = _mn(g["okmin"], v)
    return list(hours.values()), list(tools.values())


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _hour_key(record: InspectionRecord) -> str:
    ts = record.timestamp or ""
    if len(ts) >= 13 and ts[4] == "-" and ts[10] == " ":
        return ts[:13]
    return datetime.now().strftime("%Y-%m-%d %H")


class DatabaseRepository:

    # SQLite's own default page cache is only 2 MB: with a multi-GB database the random-key indexes
    # (uid, content_hash) no longer fit and every insert turns into disk reads. The agent sets this from
    # IV4_DB_CACHE_MB at start-up; small helper tools (status, monitor) keep the modest default.
    DEFAULT_CACHE_MB = 64

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
            cur.execute(f"PRAGMA cache_size=-{int(DatabaseRepository.DEFAULT_CACHE_MB) * 1024}")
            cur.execute("PRAGMA temp_store=MEMORY")
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
        with Session(self.engine, expire_on_commit=False) as session:
            row, action = self._upsert(session, record, analysis, uid, content_hash, image_file, folder)
            session.commit()
            return row, action

    def save_batch(self, items: list[dict]) -> list[tuple[int, str]]:
        """
        Save many inspections in ONE transaction (group commit), using bulk
        statements: 2 lookups + 1 multi-row INSERT + a few metric upserts per
        batch instead of ~5 statements per inspection.
        items: dicts with record, analysis, uid, content_hash, image_file, folder.
        Returns [(database_id, action)] in input order. All-or-nothing.
        """
        if not items:
            return []
        uids = [it["uid"] for it in items]
        hashes = [it["content_hash"] for it in items if it.get("content_hash")]

        with Session(self.engine, expire_on_commit=False) as session:
            existing = dict(session.execute(
                select(Inspection.uid, Inspection.id).where(Inspection.uid.in_(uids))).all())
            known_hash = {h: (i, u) for h, i, u in session.execute(
                select(Inspection.content_hash, Inspection.id, Inspection.uid)
                .where(Inspection.content_hash.in_(hashes))).all()} if hashes else {}

            out: list[tuple[int | None, str] | None] = [None] * len(items)
            new_rows, new_idx, created_pairs, batch_hash = [], [], [], {}
            for i, it in enumerate(items):
                h = it.get("content_hash")
                if h and h in known_hash and known_hash[h][1] != it["uid"]:
                    out[i] = (known_hash[h][0], "DUPLICATE")
                elif h and h in batch_hash:
                    out[i] = ("pending", batch_hash[h])            # duplicate inside this batch
                elif it["uid"] in existing:
                    row, action = self._upsert(session, it["record"], it["analysis"], it["uid"],
                                               h, it.get("image_file"), it.get("folder"))
                    session.flush()
                    out[i] = (row.id, action)
                else:
                    if h:
                        batch_hash[h] = it["uid"]
                    new_rows.append(dict(uid=it["uid"], content_hash=h, created_at=_utcnow(),
                                         updated_at=_utcnow(),
                                         **self._values(it["record"], it["analysis"],
                                                        it.get("image_file"), it.get("folder"))))
                    new_idx.append(i)
                    created_pairs.append((it["record"], it["analysis"]))

            if new_rows:
                session.execute(insert(Inspection), new_rows)
                ids = dict(session.execute(
                    select(Inspection.uid, Inspection.id).where(
                        Inspection.uid.in_([r["uid"] for r in new_rows]))).all())
                for i, r in zip(new_idx, new_rows):
                    out[i] = (ids[r["uid"]], "CREATED")
                self._write_hourly(session, created_pairs)

            for i, o in enumerate(out):
                if o and o[0] == "pending":
                    out[i] = (ids[o[1]], "DUPLICATE")
            session.commit()
        return out

    @staticmethod
    def _values(record, analysis, image_file, folder) -> dict:
        return dict(
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

    def _upsert(self, session, record, analysis, uid, content_hash, image_file, folder):
        uid = uid or record.inspection_id

        values = self._values(record, analysis, image_file, folder)

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
            return existing, "UPDATED"

        row = Inspection(uid=uid, content_hash=content_hash, **values)
        session.add(row)
        self._update_hourly(session, record, analysis)
        return row, "CREATED"


    @staticmethod
    def _update_hourly(session: Session, record: InspectionRecord, analysis: AnalysisResult) -> None:
        DatabaseRepository._write_hourly(session, [(record, analysis)])

    @staticmethod
    def _write_hourly(session: Session, pairs) -> None:
        hours, tools = aggregate_hourly(pairs)
        for h in hours:                       # few rows; first_trig needs per-row binding
            session.execute(_HOURLY_UPSERT, h)
        if tools:
            session.execute(_TOOL_UPSERT, tools)

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

    def count_pending_uploads(self, statuses: frozenset[str] | None, folder_prefix: str | None = None) -> int:
        q = select(func.count()).select_from(Inspection).where(
            Inspection.uploaded_at.is_(None), Inspection.folder.is_not(None)
        )
        if folder_prefix:
            q = q.where(Inspection.folder.startswith(folder_prefix))
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
