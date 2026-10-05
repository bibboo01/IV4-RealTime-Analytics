"""
On-site benchmark: how many IV4 sensors can THIS machine handle?

Runs on the Mini PC itself (Windows/NTFS, antivirus, real disk) in a
temporary folder - production data, database and service are not touched.

    python -m scripts.benchmark                  # both tests, ~2 minutes
    python -m scripts.benchmark --max            # only: maximum throughput
    python -m scripts.benchmark --sensors 4      # only: real-time test with 4 sensors
    python -m scripts.benchmark --dir E:\\bench   # put the test on another disk

Stop the IV4DataAgent service first for a clean number (it competes for CPU/disk).
Put --dir on the SAME disk as IV4_INCOMING_DIR / IV4_ARCHIVE_DIR so the
result reflects that disk.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.config import BASE_DIR, load_settings

RATE_PER_SENSOR = 20.0          # IV4-G500CA: ~36 ms/inspection
SAMPLE_JPG = BASE_DIR / "tests" / "mock_data" / "iv4" / "00001_03102026_181913.jpg"


def _txt(t: datetime, trig: int, ng: bool) -> str:
    return (f"Time and Date\t{t:%d/%m/%Y}\t{t:%H:%M:%S}\r\nProgram No.\t0\r\nTrigger No.\t{trig}\r\n"
            f"TIME[ms]\t36\r\nTotal Status\t{'NG' if ng else 'OK'}\r\n"
            f"Tool01:AI Differentiate\tOK\t100\t\r\n"
            f"Tool02:AI Differentiate\t{'NG' if ng else 'OK'}\t{12 if ng else 99}\t\r\n")


def _settings(base: Path, sensors: int):
    names = ",".join(f"S{i + 1:02d}" for i in range(sensors))
    s = load_settings(base_dir=base, overrides={
        "IV4_DATA_DIR": str(base / "data"), "IV4_LOG_DIR": str(base / "logs"),
        "IV4_SENSORS": names, "IV4_SETTLE_SECONDS": "1.0", "IV4_UPLOAD_ENABLED": "false",
        "IV4_LOG_LEVEL": "WARNING", "IV4_MIN_FREE_GB": "0",
    })
    s.ensure_dirs()
    return s


def test_max(base: Path, jpg: bytes, n: int = 3000) -> float:
    """Drain a pre-filled backlog as fast as possible."""
    from app.ingestion.watcher import IV4Agent

    s = _settings(base, 2)
    s.settle_seconds = 0
    old = time.time() - 600
    t0 = datetime(2026, 1, 1, 8)
    print(f"  preparing {n} inspections ...", flush=True)
    for i in range(n):
        d = s.incoming_dir / f"S{i % 2 + 1:02d}"
        t = t0 + timedelta(milliseconds=50 * i)
        stem = f"{i:05d}_{t:%d%m%Y_%H%M%S}"
        (d / f"{stem}.jpg").write_bytes(jpg)
        (d / f"{stem}.txt").write_text(_txt(t, 1000 + i, i % 30 == 0), newline="")
        for f in (d / f"{stem}.jpg", d / f"{stem}.txt"):
            os.utime(f, (old, old))

    agent = IV4Agent(s)
    start = time.time()
    while agent.repo.count() < n and time.time() - start < 600:
        agent.scan_once()
    dt = time.time() - start
    done = agent.repo.count()
    agent.pool.shutdown()
    agent.repo.dispose()
    rate = done / dt
    print(f"  processed {done}/{n} in {dt:.1f} s -> {rate:.0f} inspections/s")
    return rate


def test_realtime(base: Path, jpg: bytes, sensors: int, seconds: float) -> dict:
    """Simulate N sensors writing like an FTP server, with the real agent loop."""
    from app.ingestion.watcher import IV4Agent

    s = _settings(base, sensors)
    agent = IV4Agent(s)
    runner = threading.Thread(target=agent.run, daemon=True)
    runner.start()
    time.sleep(1.0)

    stop = threading.Event()
    produced = [0] * sensors
    peak_backlog = [0]

    def sensor(idx: int):
        d = s.incoming_dir / f"S{idx + 1:02d}"
        t0 = time.time()
        k = 0
        while not stop.is_set():
            target = t0 + k / RATE_PER_SENSOR
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
            now = datetime.now()
            stem = f"{k % 99999 + 1:05d}_{now:%d%m%Y_%H%M%S}_{k}"
            with open(d / f"{stem}.jpg", "wb") as fh:          # chunked, like FTP
                for j in range(0, len(jpg), 32768):
                    fh.write(jpg[j:j + 32768])
                    fh.flush()
            (d / f"{stem}.txt").write_text(_txt(now, 10_000 * idx + k, k % 25 == 0), newline="")
            k += 1
            produced[idx] = k

    def watch_backlog():
        while not stop.is_set():
            n = sum(1 for _, f in s.sensor_sources() for p in f.glob("*.txt"))
            peak_backlog[0] = max(peak_backlog[0], n)
            time.sleep(1)

    threads = [threading.Thread(target=sensor, args=(i,), daemon=True) for i in range(sensors)]
    threads.append(threading.Thread(target=watch_backlog, daemon=True))
    [t.start() for t in threads]
    time.sleep(seconds)
    stop.set()
    [t.join() for t in threads]
    total = sum(produced)

    deadline = time.time() + 60
    while agent.repo.count() < total and time.time() < deadline:
        time.sleep(0.5)
    catch_up = 60 - (deadline - time.time())
    done = agent.repo.count()
    agent.stop()
    runner.join(timeout=30)

    # settle (1 s) + one scan interval is the built-in minimum delay
    ok = done == total and catch_up < 5 and peak_backlog[0] < RATE_PER_SENSOR * sensors * 6
    return dict(sensors=sensors, produced=total, stored=done, peak_backlog=peak_backlog[0],
                catch_up_s=round(catch_up, 1), ok=ok,
                rate=round(total / seconds, 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="where to create the temporary test folder (default: project folder)")
    ap.add_argument("--max", action="store_true", help="only the maximum-throughput test")
    ap.add_argument("--sensors", type=int, help="only the real-time test with this many sensors")
    ap.add_argument("--seconds", type=float, default=45)
    a = ap.parse_args()

    jpg = SAMPLE_JPG.read_bytes()
    root = Path(a.dir) if a.dir else BASE_DIR
    root.mkdir(parents=True, exist_ok=True)

    print("=" * 66)
    print("IV4 Data Agent benchmark")
    print(f"  machine : {platform.node()}  {platform.system()} {platform.release()}  {platform.machine()}")
    print(f"  CPU     : {os.cpu_count()} logical cores   Python {sys.version.split()[0]}")
    print(f"  disk    : {root}  free {shutil.disk_usage(root).free / 1024**3:.0f} GB")
    print(f"  image   : {len(jpg) / 1024:.0f} KB per inspection, {RATE_PER_SENSOR:.0f} inspections/s per sensor")
    print("=" * 66)

    results = {}
    if not a.sensors:
        with tempfile.TemporaryDirectory(prefix="iv4_bench_", dir=root) as tmp:
            print("\n[1] Maximum throughput (backlog drain)")
            rate = test_max(Path(tmp), jpg)
            results["max"] = rate
            print(f"  => up to ~{rate / RATE_PER_SENSOR:.0f} sensors (theoretical, no headroom)")

    if not a.max:
        counts = [a.sensors] if a.sensors else None
        if counts is None:
            cap = max(1, int(results.get("max", 200) / RATE_PER_SENSOR * 0.6))
            counts = sorted({2, max(2, cap // 2), cap})
        print(f"\n[2] Real-time test ({a.seconds:.0f} s each, chunked writes like FTP)")
        best = 0
        for n in counts:
            with tempfile.TemporaryDirectory(prefix="iv4_bench_", dir=root) as tmp:
                r = test_realtime(Path(tmp), jpg, n, a.seconds)
            verdict = "OK  " if r["ok"] else "FAIL"
            print(f"  {verdict} {n:2d} sensors ({r['rate']:.0f}/s): stored {r['stored']}/{r['produced']}, "
                  f"peak backlog {r['peak_backlog']} files, caught up {r['catch_up_s']} s after stop")
            if r["ok"]:
                best = max(best, n)
            else:
                break
        results["realtime_ok"] = best

    print("\n" + "=" * 66)
    if "max" in results:
        print(f"Max throughput        : {results['max']:.0f} inspections/s")
    if "realtime_ok" in results:
        print(f"Real-time verified    : {results['realtime_ok']} sensor(s) at {RATE_PER_SENSOR:.0f}/s each")
    print("Recommended in production: use at most ~50% of the max (headroom for disk, antivirus,")
    print("dashboard queries, retention). Storage: ~8 GB per sensor per running hour of images.")
    print("=" * 66)


if __name__ == "__main__":
    main()
