from ina226_monitor.measurement import MeasurementData
from ina226_monitor.protocol import HEADER_LINE, LineKind, LineSplitter, parse_line

SAMPLE = [
    "188002,0.0000,-10.0,-0.125",
    "188052,0.0000,-7.5,-0.094",
    "188102,0.0000,-10.0,-0.125",
    "188152,0.0000,-7.5,-0.094",
    "188203,0.0000,-7.5,-0.094",
]


def test_ac03_only_data_rows_are_taken():
    """AC-03：ヘッダ・# 行・途中で切れた行を混ぜても、データ 5 行だけ取り込み、不正行は 1。"""
    stream = "\r\n".join(["# I2C scan...", HEADER_LINE, *SAMPLE[:3], "188253,0.00", *SAMPLE[3:]]) + "\r\n"
    data = MeasurementData()
    splitter = LineSplitter()
    kinds = []
    # 1 バイトずつ届いても行が正しく組み立てられること
    for b in stream.encode("ascii"):
        for line in splitter.feed(bytes([b])):
            p = parse_line(line)
            kinds.append(p.kind)
            if p.kind is LineKind.DATA:
                data.add(p.row.time_ms, p.row.bus_V, p.row.current_mA, 0.0)
            elif p.kind is LineKind.INVALID:
                data.invalid_count += 1
    assert data.count == 5
    assert data.invalid_count == 1
    assert kinds.count(LineKind.HEADER) == 1
    assert kinds.count(LineKind.COMMENT) == 1


def test_parse_values():
    p = parse_line("188002,0.0000,-10.0,-0.125\r\n")
    assert p.kind is LineKind.DATA
    assert p.row.time_ms == 188002
    assert p.row.bus_V == 0.0
    assert p.row.shunt_uV == -10.0
    assert p.row.current_mA == -0.125


def test_invalid_variants():
    for line in ["", "1,2,3", "1,2,3,4,5", "a,0.0,0.0,0.0", "1.5,0,0,0", "1,nan,0,0", "1,0,0,", "1,0,0,inf"]:
        assert parse_line(line).kind is LineKind.INVALID, line


def test_splitter_keeps_partial_line_until_newline():
    s = LineSplitter()
    assert s.feed(b"188002,0.00") == []
    assert s.feed(b"00,-10.0,-0.125\r\n1880") == ["188002,0.0000,-10.0,-0.125"]
    assert s.feed(b"52,0.0000,-7.5,-0.094\r\n") == ["188052,0.0000,-7.5,-0.094"]
