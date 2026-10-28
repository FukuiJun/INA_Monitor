import json
import time

from ina226_monitor.recovery import (
    RecoveryWriter,
    delete_item,
    find_recoverable,
    recover_to,
)

from .helpers import mock_data

INFO = {"board": "MAIN-RevB", "target": "REG-U3", "operator": "福井", "started_at": "2026-10-28 14:30:05"}


def test_writer_appends_and_recovers(tmp_path):
    rec = tmp_path / "recovery"
    d = mock_data(250)
    w = RecoveryWriter(rec, "20261028_143005", INFO)
    w.submit(d.slice_copy(0, 100))
    w.submit(d.slice_copy(100, 250))
    assert w.close()
    last, err, rows = w.status()
    assert rows == 250 and not err and last is not None

    csv = rec / "20261028_143005_tmp.csv"
    assert csv.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads((rec / "20261028_143005_tmp.json").read_text(encoding="utf-8")) == INFO

    items = find_recoverable(rec)
    assert len(items) == 1
    assert items[0].row_count == 250
    assert items[0].info["target"] == "REG-U3"

    out = tmp_path / "out"
    out.mkdir()
    dest = recover_to(items[0], out)
    assert dest.name == "20261028_143005_recovered.csv"
    lines = dest.read_bytes()[3:].decode("utf-8").split("\r\n")
    assert lines[0] == "elapsed_s,pc_time,bus_V,current_mA,power_mW,mAh"
    assert len(lines) - 2 == 250

    delete_item(items[0])
    assert list(rec.iterdir()) == []


def test_truncated_last_line_is_dropped(tmp_path):
    rec = tmp_path / "recovery"
    w = RecoveryWriter(rec, "20261028_143005", INFO)
    w.submit(mock_data(10).slice_copy(0))
    w.close()
    csv = rec / "20261028_143005_tmp.csv"
    with open(csv, "ab") as f:
        f.write(b"0.550,2026-10-28 14:30:17.3")  # 書き込み途中で電源断
    item = find_recoverable(rec)[0]
    assert item.row_count == 10
    dest = recover_to(item, tmp_path)
    assert dest.read_bytes().endswith(b"\r\n")
    assert len(dest.read_bytes()[3:].decode().split("\r\n")) - 2 == 10


def test_write_failure_is_retried(tmp_path, monkeypatch):
    rec = tmp_path / "recovery"
    d = mock_data(30)
    fail = {"on": True}
    orig = RecoveryWriter._append

    def flaky(self, text):
        if fail["on"]:
            raise PermissionError("locked")
        return orig(self, text)

    monkeypatch.setattr(RecoveryWriter, "_append", flaky)
    w = RecoveryWriter(rec, "20261028_143005", INFO)
    w.submit(d.slice_copy(0, 10))
    w.submit(d.slice_copy(10, 20))
    for _ in range(100):
        if w.status()[1]:
            break
        time.sleep(0.01)
    assert w.status()[1] is True  # 失敗が分かる
    fail["on"] = False
    w.submit(d.slice_copy(20, 30))  # 次の回でまとめて書く
    assert w.close()
    assert w.status()[2] == 30
    item = [i for i in find_recoverable(rec) if i.name == "20261028_143005"][0]
    assert item.row_count == 30


def test_empty_tmp_is_removed_silently(tmp_path):
    rec = tmp_path / "recovery"
    w = RecoveryWriter(rec, "20261028_143005", INFO)
    w.close()
    assert find_recoverable(rec) == []
    assert list(rec.iterdir()) == []


def test_name_collision_gets_suffix(tmp_path):
    rec = tmp_path / "recovery"
    a = RecoveryWriter(rec, "20261028_143005", INFO)
    a.close()
    b = RecoveryWriter(rec, "20261028_143005", INFO)
    b.close()
    assert b.name == "20261028_143005_2"
