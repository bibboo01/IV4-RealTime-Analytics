"""
Central configuration for IV4 Data Agent.

All values can be overridden with environment variables or a `.env`
file in the project root (KEY=VALUE per line). Real environment
variables take precedence over `.env`.
"""
from __future__ import annotations

import os
import re
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
        values[key.strip()] = _clean_value(value)
    return values


def _clean_value(value: str) -> str:
    """
    'abc'  /  "abc"           -> abc   (quotes keep '#' and spaces)
    1.0   # seconds ...       -> 1.0   (inline comment needs a space before '#')
    """
    value = value.strip()
    if value.startswith("#"):
        return ""                       # empty value followed by a comment
    if value[:1] in {'"', "'"}:
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


class ConfigError(ValueError):
    """A value in .env / environment is invalid; message names the setting."""


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
    workers: int = 0                    # file-work threads; 0 = auto (2 x CPU cores, max 16)
    batch_size: int = 200               # inspections per DB transaction

    # IV4 source
    date_format: str = "%d/%m/%Y"       # 'Time and Date' in the TXT (03/10/2026 = 3 Oct)
    sensor_id: str | None = None        # sensor name for files dropped directly in incoming/
    sensors: tuple[str, ...] = ()       # sensor subfolders incoming/<name>/ ; empty = auto-detect
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
    disk_prune_ok: bool = True          # when free < min_free_gb delete OLDEST OK images first (NG never)
    disk_check_interval: float = 300.0

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
            *(self.incoming_dir / name for name in self.sensors),
        ):
            d.mkdir(parents=True, exist_ok=True)

    def worker_count(self) -> int:
        if self.workers > 0:
            return self.workers
        return max(2, min(16, 2 * (os.cpu_count() or 2)))

    def sensor_sources(self) -> list[tuple[str, Path]]:
        """
        (sensor_id, folder) pairs to scan.

        Each IV4 writes into its own subfolder incoming/<sensor>/ (set the
        FTP destination folder per sensor). Files dropped directly in
        incoming/ belong to IV4_SENSOR_ID (single-sensor setups).
        """
        names = list(self.sensors)
        if not names and self.incoming_dir.exists():
            names = sorted(p.name for p in self.incoming_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        out = [(self.sensor_id or "IV4", self.incoming_dir)]
        out += [(n, self.incoming_dir / n) for n in names]
        return out


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

    def _num(conv, key: str, default: str):
        raw = get(key, default)
        try:
            return conv(raw)
        except ValueError:
            raise ConfigError(
                f"IV4_{key}={raw!r} is not a valid {'number' if conv is float else 'whole number'} "
                f"- fix it in .env") from None

    def _f(key: str, default: str) -> float:
        return _num(float, key, default)

    def _i(key: str, default: str) -> int:
        return _num(int, key, default)

    def _fo(key: str) -> float | None:
        return _f(key, "") if get(key, "").strip() else None

    data_dir = _path(get("DATA_DIR", "data"), base)


    return Settings(
        data_dir=data_dir,
        incoming_dir=_path(get("INCOMING_DIR", str(data_dir / "incoming")), base),
        processing_dir=_path(get("PROCESSING_DIR", str(data_dir / "processing")), base),
        error_dir=_path(get("ERROR_DIR", str(data_dir / "error")), base),
        uploaded_dir=_path(get("UPLOADED_DIR", str(data_dir / "uploaded")), base),
        archive_dir=_path(get("ARCHIVE_DIR", str(data_dir / "archive")), base),
        database_path=_path(get("DATABASE_PATH", str(data_dir / "database" / "iv4.db")), base),
        log_dir=_path(get("LOG_DIR", "logs"), base),
        scan_interval=_f("SCAN_INTERVAL", "1.0"),
        settle_seconds=_f("SETTLE_SECONDS", "1.5"),
        group_timeout=_f("GROUP_TIMEOUT", "120"),
        expected_images=_i("EXPECTED_IMAGES", "1"),
        expected_texts=_i("EXPECTED_TEXTS", "1"),
        verify_images=get("VERIFY_IMAGES", "true").lower() in {"1", "true", "yes"},
        use_polling=get("USE_POLLING", "false").lower() in {"1", "true", "yes"},
        workers=_i("WORKERS", "0"),
        batch_size=_i("BATCH_SIZE", "200"),
        date_format=get("DATE_FORMAT", "%d/%m/%Y"),
        sensor_id=get("SENSOR_ID", "IV4-01") or None,
        sensors=tuple(x.strip() for x in get("SENSORS", "").split(",") if x.strip()),
        machine_id=get("MACHINE_ID", "") or None,
        score_threshold=_fo("SCORE_THRESHOLD"),
        confidence_threshold=_fo("CONFIDENCE_THRESHOLD"),
        retention_ok_days=_i("RETENTION_OK_DAYS", "0"),
        retention_ng_days=_i("RETENTION_NG_DAYS", "0"),
        retention_rows_days=_i("RETENTION_ROWS_DAYS", "0"),
        cleanup_interval=_f("CLEANUP_INTERVAL", "3600"),
        min_free_gb=_f("MIN_FREE_GB", "20"),
        disk_prune_ok=get("DISK_PRUNE_OK", "true").lower() in {"1", "true", "yes"},
        disk_check_interval=_f("DISK_CHECK_INTERVAL", "300"),
        upload_enabled=get("UPLOAD_ENABLED", "false").lower() in {"1", "true", "yes"},
        upload_statuses=_statuses(get("UPLOAD_STATUSES", "FAIL,UNKNOWN")),
        upload_backend=get("UPLOAD_BACKEND", "gdrive").lower(),
        upload_interval=_f("UPLOAD_INTERVAL", "30"),
        upload_batch=_i("UPLOAD_BATCH", "50"),
        gdrive_auth=get("GDRIVE_AUTH", "oauth").lower(),
        gdrive_credentials=_path(get("GDRIVE_CREDENTIALS", "credentials/client_secret.json"), base),
        gdrive_token=_path(get("GDRIVE_TOKEN", "credentials/token.json"), base),
        gdrive_folder_id=get("GDRIVE_FOLDER_ID", ""),
        gdrive_root_name=get("GDRIVE_ROOT_NAME", "IV4 Data Agent"),
        log_level=get("LOG_LEVEL", "INFO").upper(),
        log_max_bytes=_i("LOG_MAX_BYTES", "10485760"),
        log_backup_count=_i("LOG_BACKUP_COUNT", "10"),
    )
