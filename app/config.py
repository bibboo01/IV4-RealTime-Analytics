"""
Central configuration for IV4 Data Agent.

All values can be overridden with environment variables or a `.env`
file in the project root (KEY=VALUE per line). Real environment
variables take precedence over `.env`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _path(value: str, base: Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p)


@dataclass
class Settings:
    data_dir: Path
    incoming_dir: Path
    processing_dir: Path
    error_dir: Path
    uploaded_dir: Path
    archive_dir: Path
    database_path: Path
    log_dir: Path

    # Ingestion timing
    scan_interval: float = 1.0          # seconds between folder scans
    settle_seconds: float = 1.5         # file size/mtime must be unchanged this long
    group_timeout: float = 120.0        # incomplete group -> error after this
    expected_images: int = 1
    expected_texts: int = 1             # real IV4: 1 image + 1 result TXT
    verify_images: bool = True          # open JPG with Pillow to catch truncation
    use_polling: bool = False           # True for network shares (SMB) where events are unreliable

    # IV4 source
    date_format: str = "%d/%m/%Y"       # 'Time and Date' in the TXT (03/10/2026 = 3 Oct)
    sensor_id: str | None = None        # stored as camera_id (TXT has no sensor id)
    machine_id: str | None = None

    # Optional extra thresholds (sensor judgement is used by default)
    score_threshold: float | None = None
    confidence_threshold: float | None = None

    # Retention (0 = keep forever). Images are kept per day/status folder.
    retention_ok_days: int = 0          # delete OK images older than N days
    retention_ng_days: int = 0          # delete NG/UNKNOWN images older than N days
    retention_rows_days: int = 0        # delete per-inspection DB rows older than N days (hourly stats are kept)
    cleanup_interval: float = 3600.0
    min_free_gb: float = 20.0           # health warning when free disk drops below this

    # Online storage (Phase 7)
    upload_enabled: bool = False
    upload_statuses: frozenset[str] | None = frozenset({"FAIL", "UNKNOWN"})  # None = upload everything
    upload_backend: str = "gdrive"      # gdrive | mock
    upload_interval: float = 30.0       # seconds between upload rounds
    upload_batch: int = 50              # max inspections per round
    gdrive_auth: str = "oauth"          # oauth (personal Gmail) | service_account (Shared Drive)
    gdrive_credentials: Path | None = None   # client_secret.json or service-account.json
    gdrive_token: Path | None = None         # OAuth refresh token (created by scripts.gdrive_auth)
    gdrive_folder_id: str = ""          # optional; empty = app creates its own root folder
    gdrive_root_name: str = "IV4 Data Agent"

    # Logging
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 10

    supported_image_ext: frozenset[str] = field(
        default_factory=lambda: frozenset({".jpg", ".jpeg"})
    )
    supported_text_ext: frozenset[str] = field(
        default_factory=lambda: frozenset({".txt"})
    )

    def ensure_dirs(self) -> None:
        for d in (
            self.incoming_dir,
            self.processing_dir,
            self.error_dir,
            self.uploaded_dir,
            self.archive_dir,
            self.database_path.parent,
            self.log_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def _statuses(value: str) -> frozenset[str] | None:
    value = value.strip().upper()
    if value in {"", "ALL", "*"}:
        return None
    return frozenset(v.strip() for v in value.split(",") if v.strip())


def load_settings(
    base_dir: Path | None = None,
    overrides: dict[str, str] | None = None,
) -> Settings:
    base = Path(base_dir) if base_dir else BASE_DIR
    env = _load_dotenv(base / ".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith("IV4_")})
    if overrides:
        env.update(overrides)

    def get(key: str, default: str) -> str:
        return env.get(f"IV4_{key}", default)

    data_dir = _path(get("DATA_DIR", "data"), base)

    conf_th = get("CONFIDENCE_THRESHOLD", "")

    return Settings(
        data_dir=data_dir,
        incoming_dir=_path(get("INCOMING_DIR", str(data_dir / "incoming")), base),
        processing_dir=_path(get("PROCESSING_DIR", str(data_dir / "processing")), base),
        error_dir=_path(get("ERROR_DIR", str(data_dir / "error")), base),
        uploaded_dir=_path(get("UPLOADED_DIR", str(data_dir / "uploaded")), base),
        archive_dir=_path(get("ARCHIVE_DIR", str(data_dir / "archive")), base),
        database_path=_path(get("DATABASE_PATH", str(data_dir / "database" / "iv4.db")), base),
        log_dir=_path(get("LOG_DIR", "logs"), base),
        scan_interval=float(get("SCAN_INTERVAL", "1.0")),
        settle_seconds=float(get("SETTLE_SECONDS", "1.5")),
        group_timeout=float(get("GROUP_TIMEOUT", "120")),
        expected_images=int(get("EXPECTED_IMAGES", "1")),
        expected_texts=int(get("EXPECTED_TEXTS", "1")),
        verify_images=get("VERIFY_IMAGES", "true").lower() in {"1", "true", "yes"},
        use_polling=get("USE_POLLING", "false").lower() in {"1", "true", "yes"},
        date_format=get("DATE_FORMAT", "%d/%m/%Y"),
        sensor_id=get("SENSOR_ID", "IV4-01") or None,
        machine_id=get("MACHINE_ID", "") or None,
        score_threshold=float(get("SCORE_THRESHOLD", "")) if get("SCORE_THRESHOLD", "") else None,
        confidence_threshold=float(conf_th) if conf_th else None,
        retention_ok_days=int(get("RETENTION_OK_DAYS", "0")),
        retention_ng_days=int(get("RETENTION_NG_DAYS", "0")),
        retention_rows_days=int(get("RETENTION_ROWS_DAYS", "0")),
        cleanup_interval=float(get("CLEANUP_INTERVAL", "3600")),
        min_free_gb=float(get("MIN_FREE_GB", "20")),
        upload_enabled=get("UPLOAD_ENABLED", "false").lower() in {"1", "true", "yes"},
        upload_statuses=_statuses(get("UPLOAD_STATUSES", "FAIL,UNKNOWN")),
        upload_backend=get("UPLOAD_BACKEND", "gdrive").lower(),
        upload_interval=float(get("UPLOAD_INTERVAL", "30")),
        upload_batch=int(get("UPLOAD_BATCH", "50")),
        gdrive_auth=get("GDRIVE_AUTH", "oauth").lower(),
        gdrive_credentials=_path(get("GDRIVE_CREDENTIALS", "credentials/client_secret.json"), base),
        gdrive_token=_path(get("GDRIVE_TOKEN", "credentials/token.json"), base),
        gdrive_folder_id=get("GDRIVE_FOLDER_ID", ""),
        gdrive_root_name=get("GDRIVE_ROOT_NAME", "IV4 Data Agent"),
        log_level=get("LOG_LEVEL", "INFO").upper(),
        log_max_bytes=int(get("LOG_MAX_BYTES", str(10 * 1024 * 1024))),
        log_backup_count=int(get("LOG_BACKUP_COUNT", "10")),
    )
