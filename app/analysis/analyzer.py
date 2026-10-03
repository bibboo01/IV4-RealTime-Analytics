from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.parser.inspection_record import InspectionRecord

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"   # not enough data to decide -> never silently PASS

OK_RESULTS = {"OK", "PASS", "GO", "1", "TRUE"}
NG_RESULTS = {"NG", "FAIL", "NOGO", "0", "FALSE"}


@dataclass
class AnalysisResult:
    """
    Result produced by the analysis engine.

    NOTE:
    Rules are currently based on Mock Data. Thresholds are configurable
    (IV4_SCORE_THRESHOLD / IV4_CONFIDENCE_THRESHOLD) and must be confirmed
    with real IV4 data and the actual business rules.
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
    score_threshold: float | None = 90.0,
    confidence_threshold: float | None = None,
) -> AnalysisResult:

    fail_reasons: list[str] = []

    result = str(record.result).strip().upper() if record.result is not None else None

    if result in NG_RESULTS:
        fail_reasons.append(f"Inspection result is {record.result}")

    if record.defect_count is not None and record.defect_count > 0:
        fail_reasons.append(f"Defect count = {record.defect_count}")

    if (
        score_threshold is not None
        and record.score is not None
        and record.score < score_threshold
    ):
        fail_reasons.append(f"Score below threshold: {record.score} < {score_threshold}")

    if (
        confidence_threshold is not None
        and record.confidence is not None
        and record.confidence < confidence_threshold
    ):
        fail_reasons.append(
            f"Confidence below threshold: {record.confidence} < {confidence_threshold}"
        )

    if fail_reasons:
        status, reasons = FAIL, fail_reasons
    elif result is None:
        status, reasons = UNKNOWN, ["Result field missing"]
    elif result not in OK_RESULTS:
        status, reasons = UNKNOWN, [f"Unrecognised result value: {record.result}"]
    else:
        status, reasons = PASS, ["No failure conditions detected"]

    return AnalysisResult(
        inspection_id=record.inspection_id,
        status=status,
        score=record.score,
        defect_count=record.defect_count,
        confidence=record.confidence,
        inspection_time_ms=record.inspection_time_ms,
        reasons=reasons,
    )
