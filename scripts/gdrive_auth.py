"""
One-time Google sign-in for the Drive uploader (OAuth mode).

Prerequisite: in Google Cloud Console
  1. Enable "Google Drive API" for your project
  2. APIs & Services -> Credentials -> Create credentials -> OAuth client ID
     -> Application type "Desktop app" -> Download JSON
  3. Save it as credentials/client_secret.json in this project
  4. OAuth consent screen: add your Gmail as a Test user
     (or publish the app, otherwise the token expires after 7 days)

Then on the Mini PC (needs a browser once):

    python -m scripts.gdrive_auth            # sign in, saves credentials/token.json
    python -m scripts.gdrive_auth --test     # also upload a small test file

The agent then refreshes the token automatically.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from app.config import load_settings
from app.upload.google_drive import OAUTH_SCOPES, GoogleDriveUploader


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="upload a test file after sign-in")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    args = ap.parse_args()

    s = load_settings()

    if s.gdrive_auth == "oauth":
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not s.gdrive_credentials.exists():
            raise SystemExit(
                f"Missing {s.gdrive_credentials}\n"
                "Download an OAuth 'Desktop app' client JSON from Google Cloud Console first.\n"
                "(A Google API key cannot upload to Drive.)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(s.gdrive_credentials), OAUTH_SCOPES)
        creds = flow.run_local_server(port=0, open_browser=not args.no_browser)
        s.gdrive_token.parent.mkdir(parents=True, exist_ok=True)
        s.gdrive_token.write_text(creds.to_json(), encoding="utf-8")
        print(f"Saved token -> {s.gdrive_token}")

    up = GoogleDriveUploader(s)
    root = up.root_id()
    print(f"Drive root folder id: {root}")
    print(f"Open: https://drive.google.com/drive/folders/{root}")

    if args.test:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "iv4_connection_test.txt"
            p.write_text("IV4 Data Agent connection test\n", encoding="utf-8")
            fid = up.upload_file(p, root)
        print(f"Test file uploaded, id={fid}")


if __name__ == "__main__":
    main()
