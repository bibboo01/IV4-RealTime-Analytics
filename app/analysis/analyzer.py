from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from app.parser.inspection_record import (
    InspectionRecord,
)


@dataclass
class AnalysisResult:
    """
    Result produced by the analysis engine.

    NOTE:
    Rules are currently based on Mock Data.
    They must be updated when real IV4 data and
    actual business rules are available.
    """

    inspection_id: str
    status: str

    score: float | None = None
    defect_count: int | None = None
    confidence: float | None = None
    inspection_time_ms: int | None = None

    reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_inspection(
    record: InspectionRecord,
) -> AnalysisResult:

    reasons: list[str] = []

    # --------------------------------------------------
    # Mock Analysis Rules
    # --------------------------------------------------

    # Rule 1:
    # Explicit NG result -> FAIL
    if record.result is not None:
        if str(record.result).upper() == "NG":
            reasons.append(
                "Inspection result is NG"
            )

            return AnalysisResult(
                inspection_id=record.inspection_id,
                status="FAIL",
                score=record.score,
                defect_count=record.defect_count,
                confidence=record.confidence,
                inspection_time_ms=record.inspection_time_ms,
                reasons=reasons,
            )

    # Rule 2:
    # Any defect -> FAIL
    if (
        record.defect_count is not None
        and record.defect_count > 0
    ):
        reasons.append(
            f"Defect count = {record.defect_count}"
        )

        return AnalysisResult(
            inspection_id=record.inspection_id,
            status="FAIL",
            score=record.score,
            defect_count=record.defect_count,
            confidence=record.confidence,
            inspection_time_ms=record.inspection_time_ms,
            reasons=reasons,
        )

    # Rule 3:
    # Mock score threshold
    if (
        record.score is not None
        and record.score < 90
    ):
        reasons.append(
            f"Score below threshold: "
            f"{record.score} < 90"
        )

        return AnalysisResult(
            inspection_id=record.inspection_id,
            status="FAIL",
            score=record.score,
            defect_count=record.defect_count,
            confidence=record.confidence,
            inspection_time_ms=record.inspection_time_ms,
            reasons=reasons,
        )

    # --------------------------------------------------
    # No failure conditions detected
    # --------------------------------------------------

    reasons.append(
        "No failure conditions detected"
    )

    return AnalysisResult(
        inspection_id=record.inspection_id,
        status="PASS",
        score=record.score,
        defect_count=record.defect_count,
        confidence=record.confidence,
        inspection_time_ms=record.inspection_time_ms,
        reasons=reasons,
    )