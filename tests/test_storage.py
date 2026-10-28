import os
import stat
import sys

import pytest

from ina226_monitor.storage import (
    MeasurementInfo,
    SaveError,
    build_note,
    build_title,
    build_txt,
    save_measurement,
)
from ina226_monitor.measurement import MeasurementData

from .helpers import mock_data

INFO = MeasurementInfo(
    board="MAIN-RevB", target="REG-U3", operator="福井",
    started_at="2026-10-28 14:30:05", ended_at="2026-10-28 14:45:05",
)


def read_csv(path):
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM 付き"
    text = raw[3:].decode("utf-8")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", ""), "改行は CRLF"
    return text.split("\r\n")[:-1]


def test_ac15_pc_time_column_switch(tmp_path):
    d = mock_data(10)
    r = save_measurement(tmp_path, "off", d, INFO, False, b"png")
    lines = read_csv(r.paths[0])
    assert lines[0] == "elapsed_s,bus_V,current_mA,power_mW,mAh"
    assert len(lines) - 1 == d.count
    assert len(lines[1].split(",")) == 5

    r = save_measurement(tmp_path, "on", d, INFO, True, b"png")
    lines = read_csv(r.paths[0])
    assert lines[0].split(",")[1] == "pc_time"
    assert len(lines[1].split(",")[1]) == len("2026-10-28 14:30:17.350")
    assert lines[1].split(",")[0] == "0.000"


def test_csv_values(tmp_path):
    d = MeasurementData(offset_mA=-0.1)
    d.add(0, 3.7012, 99.931, 0.0)
    d.add(50, 3.7012, 99.931, 0.05)
    r = save_measurement(tmp_path, "v", d, INFO, False, b"")
    lines = read_csv(r.paths[0])
    assert lines[1] == "0.000,3.7012,100.031,370.235,0.0000"
    assert lines[2] == "0.050,3.7012,100.031,370.235,0.0014"


def test_ac20_memo_controls_txt(tmp_path):
    d = mock_data(5)
    r = save_measurement(tmp_path, "a", d, MeasurementInfo(memo="  \n "), False, b"png")
    assert sorted(p.suffix for p in r.paths) == [".csv", ".png"]
    assert not (tmp_path / "a.txt").exists()

    memo = "1 行目\n2 行目"
    info = MeasurementInfo(**{**INFO.__dict__, "memo": memo})
    r = save_measurement(tmp_path, "b", d, info, False, b"png")
    assert sorted(p.suffix for p in r.paths) == [".csv", ".png", ".txt"]
    raw = (tmp_path / "b.txt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8")
    assert text.split("[メモ]\r\n", 1)[1] == "1 行目\r\n2 行目\r\n"


def test_txt_content():
    d = MeasurementData(offset_mA=-0.103)
    d.add(0, 3.7, 100.0, 0.0)
    d.invalid_count = 2
    info = MeasurementInfo(**{**INFO.__dict__, "memo": "x"})
    lines = build_txt(d, info).split("\n")
    assert lines[:12] == [
        "基板情報: MAIN-RevB",
        "測定対象: REG-U3",
        "測定者: 福井",
        "測定日時: 2026-10-28 14:30:05",
        "終了日時: 2026-10-28 14:45:05",
        "測定時間: 00:00:00",
        "サンプル数: 1",
        "不正行数: 2",
        "積算電流量: 0.0000 mAh",
        "ゼロ点補正値: -0.103 mA",
        "ソフトウェア: INA226_Monitor v1.0.0",
        "シャント抵抗: 0.080 Ω（Arduino スケッチ設定値）",
    ]
    assert lines[12:14] == ["", "[メモ]"]
    # 空欄の項目も行は出す
    assert build_txt(d, MeasurementInfo()).split("\n")[0] == "基板情報: "


def test_title_and_note():
    assert build_title(INFO) == "REG-U3 / MAIN-RevB / 福井 / 2026-10-28 14:30:05"
    assert build_title(MeasurementInfo(board="B", started_at="2026-10-28 14:30:05")) == "B / 2026-10-28 14:30:05"
    d = MeasurementData()
    for i in range(18001):
        d.add(i * 50, 3.7, 100.0, 0.0)
    assert build_note(d) == "積算 25.0000 mAh ／ 測定時間 00:15:00 ／ サンプル数 18001 ／ ゼロ点補正 なし"


def test_existing_name_gets_suffix_for_all_files(tmp_path):
    (tmp_path / "base.csv").write_text("busy")
    info = MeasurementInfo(memo="m")
    r = save_measurement(tmp_path, "base", mock_data(3), info, False, b"png")
    assert r.base == "base_2"
    assert sorted(p.name for p in r.paths) == ["base_2.csv", "base_2.png", "base_2.txt"]
    assert (tmp_path / "base.csv").read_text() == "busy"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="権限で書き込み失敗を作れない環境")
def test_write_failure_raises_and_leaves_nothing(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(SaveError) as e:
            save_measurement(folder, "x", mock_data(3), INFO, False, b"png")
        assert "x.csv に書き込めません" in str(e.value)
    finally:
        folder.chmod(stat.S_IRWXU)


def test_png_write_failure_removes_csv(tmp_path, monkeypatch):
    from pathlib import Path

    orig = Path.write_bytes

    def boom(self, data):
        if self.suffix == ".png":
            raise PermissionError("locked")
        return orig(self, data)

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(SaveError) as e:
        save_measurement(tmp_path, "x", mock_data(3), INFO, False, b"png")
    assert e.value.path.name == "x.png"
    assert list(tmp_path.iterdir()) == []
