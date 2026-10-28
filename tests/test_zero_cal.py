import pytest

from ina226_monitor.mock_serial import MockProfile, MockSerial
from ina226_monitor.zero_cal import (
    ZeroCalError,
    ZeroCalFailure,
    collect_samples,
    compute_offset,
    format_offset,
)


def test_offset_is_mean_rounded():
    assert compute_offset([-0.125, -0.094] * 10) == pytest.approx(-0.110)  # 平均 -0.1095


def test_ac22_load_detected_when_mean_6mA():
    with pytest.raises(ZeroCalError) as e:
        compute_offset([6.0] * 20)
    assert e.value.failure is ZeroCalFailure.LOAD_DETECTED


def test_not_enough_samples():
    with pytest.raises(ZeroCalError) as e:
        compute_offset([0.0] * 19)
    assert e.value.failure is ZeroCalFailure.NO_DATA


def test_format_offset():
    assert format_offset(None) == "なし"
    assert format_offset(-0.103) == "-0.103 mA"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def test_collect_from_mock_noload():
    clock = FakeClock()
    port = MockSerial(MockProfile(load=False), clock=clock, sleep=clock.sleep)
    values = collect_samples(port, clock=clock)
    assert len(values) == 20
    offset = compute_offset(values)
    assert -0.2 < offset < 0.0


def test_collect_times_out_without_data():
    clock = FakeClock()

    class Silent:
        in_waiting = 0

        def read(self, n):
            clock.sleep(0.1)
            return b""

    with pytest.raises(ZeroCalError) as e:
        collect_samples(Silent(), clock=clock)
    assert e.value.failure is ZeroCalFailure.NO_DATA
    assert clock.t <= 10.2
