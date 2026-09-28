from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
)

from app.parser.inspection_record import (
    InspectionRecord,
)

from app.analysis.analyzer import (
    AnalysisResult,
)


# ============================================================
# Database Base
# ============================================================

class Base(DeclarativeBase):
    pass


# ============================================================
# Inspection Table
# ============================================================

class Inspection(Base):

    __tablename__ = "inspection"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    inspection_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )

    timestamp: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    machine_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    camera_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    result: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    width: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    height: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    defect_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    inspection_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    analysis_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    analysis_reason: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=False,
    )


# ============================================================
# Repository
# ============================================================

class DatabaseRepository:

    def __init__(
        self,
        database_path: str | Path,
    ):

        self.database_path = Path(
            database_path
        )

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        database_url = (
            f"sqlite:///"
            f"{self.database_path.resolve()}"
        )

        self.engine = create_engine(
            database_url,
            future=True,
        )

        Base.metadata.create_all(
            self.engine
        )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    def save_inspection(
        self,
        record: InspectionRecord,
        analysis: AnalysisResult,
    ) -> Inspection:

        with Session(self.engine) as session:

            inspection = Inspection(
                inspection_id=record.inspection_id,

                timestamp=record.timestamp,

                machine_id=record.machine_id,

                camera_id=record.camera_id,

                result=record.result,

                score=record.score,

                width=record.width,

                height=record.height,

                defect_count=record.defect_count,

                inspection_time_ms=(
                    record.inspection_time_ms
                ),

                confidence=record.confidence,

                analysis_status=(
                    analysis.status
                ),

                analysis_reason=(
                    "; ".join(
                        analysis.reasons or []
                    )
                ),
            )

            session.add(inspection)

            session.commit()

            session.refresh(inspection)

            return inspection

    # --------------------------------------------------------
    # Find by inspection ID
    # --------------------------------------------------------

    def get_by_inspection_id(
        self,
        inspection_id: str,
    ) -> Inspection | None:

        with Session(self.engine) as session:

            return (
                session.query(Inspection)
                .filter(
                    Inspection.inspection_id
                    == inspection_id
                )
                .first()
            )

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------

    def count(self) -> int:

        with Session(self.engine) as session:

            return session.query(
                Inspection
            ).count()

    def save_or_update_inspection(
        self,
        record: InspectionRecord,
        analysis: AnalysisResult,
    ) -> tuple[Inspection, str]:

        with Session(self.engine) as session:

            existing = (
                session.query(Inspection)
                .filter(
                    Inspection.inspection_id
                    == record.inspection_id
                )
                .first()
            )

            if existing is not None:

                existing.timestamp = record.timestamp
                existing.machine_id = record.machine_id
                existing.camera_id = record.camera_id
                existing.result = record.result
                existing.score = record.score
                existing.width = record.width
                existing.height = record.height
                existing.defect_count = record.defect_count
                existing.inspection_time_ms = (
                    record.inspection_time_ms
                )
                existing.confidence = record.confidence

                existing.analysis_status = (
                    analysis.status
                )

                existing.analysis_reason = (
                    "; ".join(
                        analysis.reasons or []
                    )
                )

                session.commit()
                session.refresh(existing)

                return existing, "UPDATED"

            inspection = Inspection(
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
                analysis_reason="; ".join(
                    analysis.reasons or []
                ),
            )

            session.add(inspection)
            session.commit()
            session.refresh(inspection)

            return inspection, "CREATED"