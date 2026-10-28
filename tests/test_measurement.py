import pytest

from ina226_monitor.measurement import MeasurementData


def feed_constant(current_mA, total_ms, step_ms=50, offset=None, bus=3.7):
    d = MeasurementData(offset)
    for t in range(0, total_ms + 1, step_ms):
        d.add(1000 + t, bus, current_mA, t / 1000.0)
    return d


def test_ac05_constant_100mA_10h():
    d = feed_constant(100.0, 36_000_000)
    assert d.mAh == pytest.approx(1000.0, abs=1e-4)
    assert f"{d.mAh:.4f}" == "1000.0000"


def test_ac05_negative_current_kept_signed():
    d = feed_constant(-50.0, 36_000_000)
    assert f"{d.mAh:.4f}" == "-500.0000"


def test_trapezoid_with_uneven_steps():
    d = MeasurementData()
    d.add(0, 3.0, 0.0, 0.0)
    d.add(50, 3.0, 100.0, 0.05)  # (0+100)/2*50 ms
    d.add(101, 3.0, 100.0, 0.101)  # 100*51 ms
    assert d.mAh == pytest.approx((2500 + 5100) / 3_600_000)


def test_ac06_power_is_bus_times_corrected_current():
    d = MeasurementData(offset_mA=-0.1)
    d.add(0, 3.7012, 99.931, 0.0)
    last = d.last()
    assert f"{last['power_mW']:.3f}" == f"{3.7012 * (99.931 + 0.1):.3f}"
    assert f"{last['power_mW']:.3f}" == "370.235"


def test_ac22_offset_is_subtracted():
    d = MeasurementData(offset_mA=-0.100)
    d.add(0, 3.3, 9.900, 0.0)
    assert f"{d.last()['current_mA']:.3f}" == "10.000"


def test_elapsed_starts_at_zero():
    d = MeasurementData()
    d.add(188002, 0.0, 0.0, 100.0)
    d.add(188052, 0.0, 0.0, 100.05)
    assert list(d.column("elapsed_s")) == [0.0, 0.05]


def test_arduino_reset_splits_integration_and_continues_elapsed():
    d = MeasurementData()
    d.add(10_000, 3.0, 100.0, 1000.0)
    d.add(10_050, 3.0, 100.0, 1000.05)
    before = d.mAh
    r = d.add(1_200, 3.0, 100.0, 1002.05)  # リセット後、PC 時刻で 2 秒後
    assert r.reset_detected
    assert d.mAh == before  # 区切りをまたぐ区間は積算しない
    assert d.last()["elapsed_s"] == pytest.approx(2.05)
    d.add(1_250, 3.0, 100.0, 1002.10)
    assert d.mAh == pytest.approx(before + 100 * 50 / 3_600_000)
    assert d.last()["elapsed_s"] == pytest.approx(2.10)
    assert d.reset_count == 1


def test_growth_keeps_data():
    d = MeasurementData()
    n = MeasurementData._INITIAL_CAPACITY * 2 + 5
    for i in range(n):
        d.add(i * 50, 1.0, float(i), i * 0.05)
    assert d.count == n
    assert d.column("current_mA")[-1] == n - 1
    assert d.column("current_mA")[1000] == 1000
