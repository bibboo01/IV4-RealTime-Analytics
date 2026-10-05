"""Google Drive uploader tests against an in-memory fake of the Drive v3 API."""
import itertools
import re
import threading

import pytest

from app.ingestion.watcher import IV4Agent
from app.upload.google_drive import FOLDER_MIME, DriveConfigError, GoogleDriveUploader, build_drive_service
from app.upload.queue import UploadWorker, pending_uploads, process_upload_queue
from tests.conftest import write_inspection


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeDrive:
    """Just enough of service.files() for the uploader."""

    def __init__(self):
        self.items = {}            # id -> {name, mimeType, parents, size}
        self._ids = itertools.count(1)
        self.fail_next_create = 0
        self.calls = 0

    def files(self):
        return self

    def list(self, q, **kw):
        self.calls += 1
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", q).group(1).replace("\\'", "'")
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        want_folder = f"mimeType = '{FOLDER_MIME}'" in q
        hits = [
            {"id": i, "name": f["name"], "size": str(f["size"])}
            for i, f in self.items.items()
            if f["name"] == name and parent in f["parents"]
            and (f["mimeType"] == FOLDER_MIME) == want_folder
        ]
        return _Req(lambda: {"files": hits})

    def create(self, body, fields, media_body=None, **kw):
        self.calls += 1

        def do():
            if self.fail_next_create:
                self.fail_next_create -= 1
                raise ConnectionError("network down")
            fid = f"id{next(self._ids)}"
            size = len(open(media_body._filename, "rb").read()) if media_body else 0
            self.items[fid] = {
                "name": body["name"],
                "mimeType": body.get("mimeType", "file"),
                "parents": body["parents"],
                "size": size,
            }
            return {"id": fid}
        return _Req(do)

    def update(self, fileId, media_body, fields, **kw):
        def do():
            self.items[fileId]["size"] = len(open(media_body._filename, "rb").read())
            return {"id": fileId}
        return _Req(do)

    def tree(self):
        def path(i):
            f = self.items[i]
            p = f["parents"][0]
            return (path(p) + "/" if p in self.items else "") + f["name"]
        return sorted(path(i) for i in self.items)


@pytest.fixture
def drive():
    return FakeDrive()


def _done_inspections(settings, repo, clock, ids):
    agent = IV4Agent(settings, repo, clock=clock)
    for i in ids:
        write_inspection(settings.incoming_dir, i)
    for _ in range(3):
        agent.scan_once()
        clock.advance(1.1)
    agent.scan_once()
    settings.upload_statuses = None   # mock inspections are OK -> upload everything in these tests
    assert len(pending_uploads(settings, repo)) == len(ids)


def test_uploads_into_date_and_uid_folders(settings, repo, clock, drive):
    _done_inspections(settings, repo, clock, ["001", "002"])
    up = GoogleDriveUploader(settings, service=drive)

    assert process_upload_queue(settings, up.upload_inspection, repo=repo) == 2
    tree = drive.tree()
    assert tree[0] == "IV4 Data Agent"
    uploaded = [t for t in tree if t.endswith(".jpg")]
    assert len(uploaded) == 2
    assert re.match(r"IV4 Data Agent/\d{4}-\d{2}-\d{2}/001__\d+T\d+/001\.jpg", uploaded[0])
    # 3 data files + manifest per inspection
    assert sum(t.count("/") == 3 for t in tree) == 8
    assert pending_uploads(settings, repo) == []

    # database records where it went
    row = repo.get_by_inspection_id("001")
    assert row.upload_ref.startswith("id") and row.uploaded_at


def test_root_folder_is_created_once_and_remembered(settings, drive):
    up1 = GoogleDriveUploader(settings, service=drive)
    r1 = up1.root_id()
    up2 = GoogleDriveUploader(settings, service=drive)    # e.g. after restart
    assert up2.root_id() == r1
    assert sum(1 for f in drive.items.values() if f["name"] == "IV4 Data Agent") == 1


def test_network_failure_is_retried_without_duplicates(settings, repo, clock, drive):
    _done_inspections(settings, repo, clock, ["003"])
    up = GoogleDriveUploader(settings, service=drive)
    up.root_id()
    drive.fail_next_create = 2          # round 1 fails on day folder, round 2 on uid folder
    assert process_upload_queue(settings, up.upload_inspection, repo=repo) == 0
    assert process_upload_queue(settings, up.upload_inspection, repo=repo) == 0
    assert len(pending_uploads(settings, repo)) == 1

    assert process_upload_queue(settings, up.upload_inspection, repo=repo) == 1
    names = [f["name"] for f in drive.items.values()]
    assert names.count("003.jpg") == 1 and names.count("003.txt") == 1
    assert sum(1 for n in names if n.startswith("003__")) == 1


def test_reupload_skips_existing_files(settings, repo, clock, drive):
    _done_inspections(settings, repo, clock, ["004"])
    up = GoogleDriveUploader(settings, service=drive)
    from app.ingestion.record import load_manifest
    uid, folder = pending_uploads(settings, repo)[0]
    manifest = load_manifest(folder)
    up.upload_inspection(folder, manifest)
    n = len(drive.items)
    up.upload_inspection(folder, manifest)   # crash before manifest saved -> retry
    assert len(drive.items) == n


def test_missing_oauth_token_is_config_error(settings):
    settings.gdrive_auth = "oauth"
    with pytest.raises(DriveConfigError, match="run gdrive-auth"):
        build_drive_service(settings)


def test_service_account_requires_folder(settings, tmp_path):
    settings.gdrive_auth = "service_account"
    settings.gdrive_credentials = tmp_path / "sa.json"
    settings.gdrive_credentials.write_text("{}")
    settings.gdrive_folder_id = ""
    with pytest.raises(DriveConfigError, match="FOLDER_ID"):
        build_drive_service(settings)


def test_worker_survives_config_error_and_keeps_items_pending(settings, repo, clock):
    _done_inspections(settings, repo, clock, ["005"])
    settings.upload_interval = 0.05
    stop = threading.Event()
    w = UploadWorker(settings, stop)          # no token -> DriveConfigError
    w.start()
    threading.Event().wait(0.3)
    stop.set()
    w.join(timeout=5)
    assert not w.is_alive()
    assert "run gdrive-auth" in w.stats["last_upload_error"]
    assert w.stats["pending"] == 1


def test_worker_uploads_in_background(settings, repo, clock, drive):
    _done_inspections(settings, repo, clock, ["006", "007"])
    settings.upload_interval = 0.05
    stop = threading.Event()
    w = UploadWorker(settings, stop, uploader=GoogleDriveUploader(settings, service=drive).upload_inspection)
    w.start()
    for _ in range(50):
        if w.stats["uploaded"] == 2:
            break
        threading.Event().wait(0.05)
    stop.set()
    w.join(timeout=5)
    assert w.stats["uploaded"] == 2 and w.stats["pending"] == 0
