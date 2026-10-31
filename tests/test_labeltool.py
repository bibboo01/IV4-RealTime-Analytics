"""run labeltool: accuracy straight from label_sample files, image lookup by name, the local web page."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from openpyxl import Workbook

from app import labeltool

HEAD = ["open image", "uid", "timestamp", "sensor", "sensor_result", "score", "failing_tools", "image_file", "folder",
        "filled_by", "truth"]


def _book(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "label"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _row(i, sensor, truth, day="13102026", hh="05", tools="", filled=""):
    name = f"{i:05d}_{day}_{hh}1030"
    uid = f"{name}__20261012T22{i:04d}"
    d = f"{day[4:]}-{day[2:4]}-{day[:2]}"
    folder = f"{d}/{'OK' if sensor == 'OK' else 'NG'}/{hh}/{uid}"
    return ["=HYPERLINK(\"x\",\"open\")", uid, f"{d} {hh}:10:30", "IV4-01", sensor, 90, tools, f"{name}.jpeg", folder,
            filled, truth]


def _rows():
    rows = [_row(i, "OK", "OK") for i in range(48)] + [_row(48, "OK", "NG"), _row(49, "OK", "")]
    rows += [_row(100 + i, "NG", "NG", tools="T1") for i in range(40)]
    rows += [_row(200 + i, "NG", "OK", tools="T2", filled="auto: T1=T2") for i in range(10)]
    return rows


def test_compute_matches_run_sensor_formulas(tmp_path):
    p = _book(tmp_path / "s.xlsx", _rows())
    rows = labeltool.read_rows(p.read_bytes(), p.name)
    st = labeltool.compute(rows)
    assert (st["tn"], st["fn"], st["fp"], st["tp"]) == (48, 1, 10, 40)
    assert st["blank"] == 1 and st["checked"] == 99
    assert st["false_reject"]["pct"] == 20.0            # 10 of the 50 it rejected
    assert round(st["missed_defect"]["pct"], 2) == round(100 / 49, 2)
    assert st["fr_tools"] == [("T2", 10)]
    w = labeltool.compute(rows, pop_ok=9800, pop_ng=200)["weighted"]
    wrong = 9800 * 1 / 49 + 200 * 10 / 50
    assert abs(w["accuracy"] - 100 * (1 - wrong / 10000)) < 1e-9
    assert labeltool.compute(rows, person_only=True)["fp"] == 0       # the auto-filled rows are left out


def test_text_mode_and_csv(tmp_path, capsys):
    p = _book(tmp_path / "s.xlsx", _rows())
    assert labeltool.run([str(p), "--text", "--pop-ok", "9800", "--pop-ng", "200"]) == 0
    out = capsys.readouterr().out
    assert "Estimated on all 10,000 pieces" in out and "T2 x10" in out
    c = tmp_path / "s.csv"
    c.write_text("uid,sensor_result,truth\na,OK,ok\nb,NG,Fail\n", encoding="utf-8")
    st = labeltool.compute(labeltool.read_rows(c.read_bytes(), c.name))
    assert (st["tn"], st["tp"]) == (1, 1)


def test_images_found_by_folder_and_by_name(tmp_path):
    root = tmp_path / "img"
    r = _row(7, "NG", "NG")
    d = root / r[8]
    d.mkdir(parents=True)
    (d / r[7]).write_bytes(b"jpg")
    img = labeltool.Images(root)
    row = dict(zip(HEAD, r))
    assert img.for_row(row) == f"{r[8]}/{r[7]}"
    row["folder"] = "moved/away"                          # folder column wrong -> found by the date/hour in the name
    assert img.for_row(row) == f"{r[8]}/{r[7]}"
    assert img.by_name("00007_13102026") == [f"{r[8]}/{r[7]}"]
    assert img.by_name("nothing-like-this") == []
    assert not img.inside(root / ".." / "secret.jpeg")


def test_web_page_search_and_image(tmp_path):
    root = tmp_path / "img"
    rows = _rows()
    for r in rows[:3]:
        (root / r[8]).mkdir(parents=True)
        (root / r[8] / r[7]).write_bytes(b"\xff\xd8jpeg")
    (tmp_path / "secret.jpeg").write_bytes(b"no")
    state = labeltool.State(labeltool.Images(root))
    p = _book(tmp_path / "s.xlsx", rows)
    state.add(p.name, p.read_bytes())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), labeltool.make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st = json.loads(urllib.request.urlopen(base + "/api/state?pop_ok=9800&pop_ng=200").read())
        assert st["stats"]["tp"] == 40 and st["stats"]["weighted"]
        res = json.loads(urllib.request.urlopen(base + "/api/search?q=00001_13102026").read())
        assert res["rows"][0]["img"] and res["rows"][0]["kind"] == "TN"
        assert urllib.request.urlopen(base + "/img?p=" + res["rows"][0]["img"]).read() == b"\xff\xd8jpeg"
        fp = json.loads(urllib.request.urlopen(base + "/api/rows?kind=FP").read())
        assert len(fp) == 10
        try:
            urllib.request.urlopen(base + "/img?p=../secret.jpeg")
            raise AssertionError("served a file outside the image folder")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        assert urllib.request.urlopen(base + "/api/export").read()[:2] == b"PK"
    finally:
        srv.shutdown()
        srv.server_close()


def _piece(root, day, st, hh, i, total=None, tools=("OK", "OK")):
    """One piece as the agent archives it: <day>/<OK|NG>/<hour>/<uid>/{image, IV4 txt (UTF-16), manifest}."""
    dd = f"{day[8:]}{day[5:7]}{day[:4]}"
    name = f"{i:05d}_{dd}_{hh}1030"
    d = root / day / st / hh / f"{name}__20261012T22{i:04d}"
    d.mkdir(parents=True)
    (d / f"{name}.jpeg").write_bytes(b"\xff\xd8jpeg")
    lines = [f"Time and Date\t{day[8:]}/{day[5:7]}/{day[:4]}\t{hh}:10:30", "Program No.\t0", f"Trigger No.\t{i}",
             "TIME[ms]\t35", f"Total Status\t{total or st}"]
    lines += [f"Tool0{k + 1}:AI Differentiate\t{t}\t{40 if t == 'NG' else 99}\t" for k, t in enumerate(tools)]
    (d / f"{name}.txt").write_bytes("\r\n".join(lines).encode("utf-16"))
    (d / "manifest.json").write_text(json.dumps({"sensor_id": "IV4-01"}), encoding="utf-8")
    return d


def test_system_labels_from_image_folder_then_person_labels(tmp_path):
    root = tmp_path / "img"
    for i in range(30):
        _piece(root, "2026-10-13", "OK", "05", i)
    for i in range(30, 40):
        _piece(root, "2026-10-13", "NG", "05", i, tools=("NG", "OK"))
    for i in range(40, 50):
        _piece(root, "2026-10-14", "NG", "14", i, tools=("NG", "NG"))
    (root / "2026-10-13" / "OK" / "05" / "053000_00000_flat.jpeg").write_bytes(b"x")   # flat gallery copy: ignored
    assert labeltool.days_in(root) == ["2026-10-14", "2026-10-13"]
    rows, head = labeltool.make_sample(root, ["2026-10-13"], 20, seed=1)
    assert len(rows) == 20 and sum(r["sensor_result"] == "NG" for r in rows) == 10
    ng = next(r for r in rows if r["sensor_result"] == "NG")
    assert ng["failing_tools"] == "T1" and ng["tool1_value"] == "40" and ng["score"] == "40"
    assert ng["system_truth"] == "" and ng["sensor"] == "IV4-01" and ng["timestamp"] == "2026-10-13 05:10:30"
    assert head[-2:] == ["system_truth", "truth"] and "tool2_status" in head
    rows, _ = labeltool.make_sample(root, ["2026-10-14"], 6, seed=1)     # no OK that day -> all from NG
    assert len(rows) == 6 and rows[0]["system_truth"] == "NG"
    assert labeltool.make_sample(root, ["2026-10-13"], 10, hours=(6, 8))[0] == []

    out = tmp_path / "labels"
    state = labeltool.State(labeltool.Images(root), out)
    name, n = state.new_sample(["2026-10-13", "2026-10-14"], 20, None, labeltool.datetime(2026, 10, 31, 9, 0))
    assert n == 20 and (out / name).is_file()
    f = state.files[name]
    for r in f.rows:                      # the person agrees except on two NG pieces that were really OK
        assert state.set_truth(name, r["uid"], r["sensor_result"]) == ""
    wrong = [r["uid"] for r in f.rows if r["sensor_result"] == "NG"][:2]
    for u in wrong:
        state.set_truth(name, u, "OK")
    again = labeltool.read_rows((out / name).read_bytes(), name)          # saved into the file
    st = labeltool.compute(again)
    assert (st["tp"], st["fp"], st["tn"], st["fn"]) == (8, 2, 10, 0)
    assert labeltool.Images(root).for_row(again[0])
    assert state.set_truth(name, "nope", "OK") == "uid not in the file"
