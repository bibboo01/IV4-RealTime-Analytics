from pathlib import Path
import time
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.ingestion.matcher import process_group

from app.pipeline import process_inspection

BASE_DIR = Path(__file__).resolve().parents[2]

WATCH_FOLDER = BASE_DIR / "data" / "incoming"
PROCESSING_FOLDER = BASE_DIR / "data" / "processing"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".txt"}

STABILITY_CHECK_INTERVAL = 0.5
STABILITY_REQUIRED_CHECKS = 3
STABILITY_TIMEOUT = 30


class IV4FileHandler(FileSystemEventHandler):

    def __init__(self):
        super().__init__()

        self.processing_groups = set()

        self.lock = threading.Lock()

    def get_group_id(self, file_path: Path) -> str:
        return file_path.stem.split("_")[0]

    def wait_until_stable(
        self,
        file_path: Path,
    ) -> bool:

        start_time = time.time()

        previous_size = -1
        stable_count = 0

        while True:

            # -----------------------------------------
            # File may have been moved by another event
            # -----------------------------------------

            if not file_path.exists():

                print(
                    f"[SKIP] "
                    f"File no longer exists: "
                    f"{file_path.name}"
                )

                return False

            current_size = file_path.stat().st_size

            print(
                f"[CHECKING] "
                f"name={file_path.name} "
                f"size={current_size} bytes"
            )

            if current_size == previous_size:

                stable_count += 1

            else:

                stable_count = 0

            if stable_count >= STABILITY_REQUIRED_CHECKS:

                return True

            previous_size = current_size

            if (
                time.time() - start_time
                >= STABILITY_TIMEOUT
            ):

                return False

            time.sleep(
                STABILITY_CHECK_INTERVAL
            )

    def on_created(self, event):

        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if (
            file_path.suffix.lower()
            not in SUPPORTED_EXTENSIONS
        ):
            return

        group_id = self.get_group_id(file_path)

        # -----------------------------------------
        # Group Lock
        # -----------------------------------------

        with self.lock:

            if group_id in self.processing_groups:

                print(
                    f"[SKIP] "
                    f"Group already processing: "
                    f"{group_id}"
                )

                return

            self.processing_groups.add(group_id)

        try:

            print()
            print(
                f"[NEW FILE] "
                f"name={file_path.name} "
                f"group={group_id}"
            )

            is_stable = self.wait_until_stable(
                file_path
            )

            if not is_stable:

                print(
                    f"[SKIP] "
                    f"File not ready: "
                    f"{file_path.name}"
                )

                return

            print(
                f"[READY] "
                f"name={file_path.name} "
                f"size={file_path.stat().st_size} bytes"
            )

            print(
                f"[MATCHER] "
                f"Checking group={group_id}..."
            )

            result = process_group(
                group_id=group_id,
                incoming_folder=WATCH_FOLDER,
                processing_folder=PROCESSING_FOLDER,
            )

            if result == "COMPLETE":

                print(
                    f"[COMPLETE] "
                    f"group={group_id}"
                )

                pipeline_result = process_inspection(
                    inspection_id=group_id,
                    processing_folder=PROCESSING_FOLDER,
                )

                if pipeline_result == "SUCCESS":

                    print(
                        f"[DONE] "
                        f"group={group_id}"
                    )

                else:

                    print(
                        f"[PIPELINE FAILED] "
                        f"group={group_id}"
                    )


            elif result == "WAITING":

                print(
                    f"[WAITING] "
                    f"group={group_id}"
                )

            elif result == "SKIP":

                print(
                    f"[SKIP] "
                    f"group={group_id}"
                )

        finally:

            with self.lock:

                self.processing_groups.discard(
                    group_id
                )


def main():

    WATCH_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    PROCESSING_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 60)
    print("IV4 Data Agent")
    print("=" * 60)

    print(
        f"Watching  : {WATCH_FOLDER}"
    )

    print(
        f"Processing: {PROCESSING_FOLDER}"
    )

    print(
        "Supported : JPG, JPEG, TXT"
    )

    print(
        f"Stability : "
        f"{STABILITY_REQUIRED_CHECKS} checks × "
        f"{STABILITY_CHECK_INTERVAL}s"
    )

    print("Press Ctrl+C to stop.")

    print("=" * 60)

    event_handler = IV4FileHandler()

    observer = Observer()

    observer.schedule(
        event_handler,
        str(WATCH_FOLDER),
        recursive=False,
    )

    observer.start()

    try:

        while True:
            time.sleep(1)

    except KeyboardInterrupt:

        print(
            "\nStopping IV4 Data Agent..."
        )

        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()