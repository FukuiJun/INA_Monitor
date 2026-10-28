from ina226_monitor.measurement import MeasurementData
from ina226_monitor.mock_serial import MockProfile, generate_lines
from ina226_monitor.protocol import LineKind, parse_line

PC_T0 = 1_793_165_405.0  # 2026-10-28 頃


def mock_data(n: int, offset=None, profile=MockProfile()) -> MeasurementData:
    """模擬シリアルの行を解析して MeasurementData を作る。"""
    d = MeasurementData(offset)
    for t, line in generate_lines(n, profile):
        p = parse_line(line)
        if p.kind is LineKind.DATA:
            d.add(p.row.time_ms, p.row.bus_V, p.row.current_mA, PC_T0 + t)
        elif p.kind is LineKind.INVALID:
            d.invalid_count += 1
    return d
