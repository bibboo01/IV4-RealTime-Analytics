"""
Google Drive uploader.

Authentication
--------------
A Google *API key* can NOT upload to Drive (it only reads public files).
Two supported modes:

* ``oauth`` (default, personal Gmail): create an OAuth "Desktop app" client
  in Google Cloud Console, save it as credentials/client_secret.json, then
  run ``python -m scripts.gdrive_auth`` once on the Mini PC to sign in.
  A refresh token is stored in credentials/token.json and reused.
  Scope ``drive.file`` = the agent can only see files it created itself.

* ``service_account`` (Google Workspace): service-account JSON + a folder
  in a *Shared Drive* shared with the service account
  (IV4_GDRIVE_FOLDER_ID). Service accounts have no My Drive quota.

Layout in Drive
---------------
    <root>/<YYYY-MM-DD>/<uid>/{image, txt, result txt, manifest.json}

Uploading is idempotent: folders are found-or-created and files that
already exist in the target folder are skipped, so a retry after a network
error never creates duplicates.
"""
from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SA_SCOPES = ["https://www.googleapis.com/auth/drive"]

_COMMON = {"supportsAllDrives": True}
_LIST = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


class DriveConfigError(RuntimeError):
    """Credentials missing / invalid – needs a human, retrying will not help."""


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_drive_service(settings: Settings):
    """Create an authenticated Drive v3 client from settings."""
    from googleapiclient.discovery import build

    if settings.gdrive_auth == "service_account":
        from google.oauth2 import service_account

        if not settings.gdrive_credentials or not settings.gdrive_credentials.exists():
            raise DriveConfigError(f"service account JSON not found: {settings.gdrive_credentials}")
        if not settings.gdrive_folder_id:
            raise DriveConfigError(
                "IV4_GDRIVE_FOLDER_ID is required for service_account "
                "(a folder in a Shared Drive shared with the service account)"
            )
        creds = service_account.Credentials.from_service_account_file(
            str(settings.gdrive_credentials), scopes=SA_SCOPES
        )
    else:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        token = settings.gdrive_token
        if not token or not token.exists():
            raise DriveConfigError(
                f"OAuth token not found ({token}). Run once on this PC: python -m scripts.gdrive_auth"
            )
        creds = Credentials.from_authorized_user_file(str(token), OAUTH_SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise DriveConfigError("OAuth token invalid. Re-run: python -m scripts.gdrive_auth")

    return build("drive", "v3", credentials=creds, cache_discovery=False)


class GoogleDriveUploader:

    def __init__(self, settings: Settings, service=None):
        self.settings = settings
        self._service = service
        self._folder_cache: dict[tuple[str, str], str] = {}
        self._root_id: str | None = settings.gdrive_folder_id or None

    # --------------------------------------------------------
    @property
    def service(self):
        if self._service is None:
            self._service = build_drive_service(self.settings)
        return self._service

    def _state_file(self) -> Path:
        base = self.settings.gdrive_token.parent if self.settings.gdrive_token else self.settings.log_dir
        return base / "drive_state.json"

    def root_id(self) -> str:
        if self._root_id:
            return self._root_id
        state = self._state_file()
        if state.exists():
            try:
                self._root_id = json.loads(state.read_text(encoding="utf-8"))["root_id"]
                return self._root_id
            except (KeyError, ValueError, OSError):
                pass
        self._root_id = self.ensure_folder(self.settings.gdrive_root_name, "root")
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"root_id": self._root_id}), encoding="utf-8")
        log.info("[GDRIVE] root folder '%s' id=%s", self.settings.gdrive_root_name, self._root_id)
        return self._root_id

    # --------------------------------------------------------
    def _find(self, name: str, parent: str, folder: bool) -> dict | None:
        mime = f"mimeType = '{FOLDER_MIME}'" if folder else f"mimeType != '{FOLDER_MIME}'"
        res = self.service.files().list(
            q=f"name = '{_q(name)}' and '{parent}' in parents and {mime} and trashed = false",
            fields="files(id, name, size)",
            pageSize=10,
            **_LIST,
        ).execute(num_retries=3)
        files = res.get("files", [])
        return files[0] if files else None

    def ensure_folder(self, name: str, parent: str) -> str:
        key = (parent, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        found = self._find(name, parent, folder=True)
        if found:
            fid = found["id"]
        else:
            fid = self.service.files().create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id",
                **_COMMON,
            ).execute(num_retries=3)["id"]
        self._folder_cache[key] = fid
        return fid

    def upload_file(self, path: Path, parent: str) -> str:
        from googleapiclient.http import MediaFileUpload

        existing = self._find(path.name, parent, folder=False)
        if existing and int(existing.get("size", -1)) == path.stat().st_size:
            return existing["id"]

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(path), mimetype=mime, resumable=path.stat().st_size > 5 * 1024 * 1024)
        if existing:  # same name, different size -> overwrite content
            return self.service.files().update(
                fileId=existing["id"], media_body=media, fields="id", **_COMMON
            ).execute(num_retries=3)["id"]
        return self.service.files().create(
            body={"name": path.name, "parents": [parent]},
            media_body=media,
            fields="id",
            **_COMMON,
        ).execute(num_retries=3)["id"]

    # --------------------------------------------------------
    def upload_inspection(self, inspection_folder: Path, manifest: dict) -> str:
        """Upload one processing/<uid>/ folder. Returns the Drive folder id."""
        uid = manifest.get("uid", inspection_folder.name)
        day = (manifest.get("created_at") or "")[:10] or "unknown-date"

        day_id = self.ensure_folder(day, self.root_id())
        folder_id = self.ensure_folder(uid, day_id)

        files = sorted(
            p for p in inspection_folder.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        )
        for f in files:
            self.upload_file(f, folder_id)

        log.info("[GDRIVE] uploaded uid=%s files=%d folder=%s", uid, len(files), folder_id)
        return folder_id
