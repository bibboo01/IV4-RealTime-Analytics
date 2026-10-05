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

    run gdrive-auth          # sign in, saves credentials/token.json
    run gdrive-auth --test     # also upload a small test file

Changing the Google account:

    run gdrive-switch --test   # forget the old account, sign in with the new one
    run gdrive-logout          # forget the account only (token + saved Drive/Sheets ids)

Stop the agent first. The old account's files stay in its Drive.

The agent then refreshes the token automatically.
"""
from __future__ import annotations

import argparse
import tempfile
import threading
import webbrowser
from pathlib import Path

from app.config import load_settings
from app.upload.google_drive import OAUTH_SCOPES, GoogleDriveUploader


def sign_in(flow, open_browser: bool = True):
    """
    Run the local-server sign-in. The link is always printed first, and the browser is opened from a
    background thread: on some Windows PCs webbrowser.open() blocks, which used to hide the link and
    leave the window looking stuck.
    """
    if open_browser:
        original = flow.authorization_url

        def authorization_url(**kwargs):
            url, state = original(**kwargs)
            threading.Thread(target=webbrowser.open, args=(url,), kwargs={"new": 1}, daemon=True).start()
            return url, state

        flow.authorization_url = authorization_url
    try:
        return flow.run_local_server(
            port=0, open_browser=False,
            authorization_prompt_message="Sign-in link (copy into a browser on this PC if none opened):\n{url}\n",
            success_message="IV4 Data Agent: sign-in complete. You can close this tab.")
    except KeyboardInterrupt:
        raise SystemExit("Sign-in cancelled - nothing saved. Run: run gdrive-auth --test") from None


def state_files(s) -> list[Path]:
    """Everything tied to the signed-in account: token + saved Drive folder / Sheet ids."""
    base = s.gdrive_token.parent
    return [s.gdrive_token, base / "drive_state.json", base / "sheets_state.json"]


def forget_account(s) -> list[str]:
    removed = []
    for f in state_files(s):
        if f.exists():
            f.unlink()
            removed.append(f.name)
    return removed


def _agent_running(s) -> str | None:
    from app.cli import InstanceLock
    return InstanceLock(s.log_dir).running_pid()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", action="store_true", help="forget the current Google account, then sign in again")
    ap.add_argument("--logout", action="store_true", help="forget the current Google account and exit")
    ap.add_argument("--test", action="store_true", help="upload a test file after sign-in")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    args = ap.parse_args()

    s = load_settings()

    if args.switch or args.logout:
        if s.gdrive_auth != "oauth":
            raise SystemExit("Only for OAuth sign-in (IV4_GDRIVE_AUTH=oauth).")
        pid = _agent_running(s)
        if pid:
            raise SystemExit(f"The agent is running (pid {pid}). Stop it first, then run this again.")
        removed = forget_account(s)
        print("Forgot the previous Google account: " + (", ".join(removed) if removed else "nothing was saved"))
        if args.logout:
            print("Sign in again with: run gdrive-auth --test")
            return

    if s.gdrive_auth == "oauth":
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not s.gdrive_credentials.exists():
            raise SystemExit(
                f"Missing {s.gdrive_credentials}\n"
                "Download an OAuth 'Desktop app' client JSON from Google Cloud Console first.\n"
                "(A Google API key cannot upload to Drive.)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(s.gdrive_credentials), OAUTH_SCOPES)
        print("Waiting for Google sign-in. The sign-in link is shown below and a browser is opened for you;\n"
              "if no browser appears, copy the link into a browser on THIS computer.\n"
              "Keep this window open until 'Saved token' appears (Ctrl+C cancels).\n")
        creds = sign_in(flow, open_browser=not args.no_browser)
        s.gdrive_token.parent.mkdir(parents=True, exist_ok=True)
        s.gdrive_token.write_text(creds.to_json(), encoding="utf-8")
        print(f"Saved token -> {s.gdrive_token}")

    up = GoogleDriveUploader(s)
    try:
        user = up.service.about().get(fields="user(emailAddress,displayName)").execute(num_retries=3)["user"]
        print(f"Signed in as: {user.get('displayName', '')} <{user.get('emailAddress', '?')}>")
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"(could not read the account name: {exc})")
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
