"""PNG 出力と、8 時間分（576,000 サンプル）の性能（AC-13 の自動テスト部分）。"""

import time

import numpy as np
import pytest

from ina226_monitor.decimate import minmax_decimate, visible_slice, zero_based_range
from ina226_monitor.storage import MeasurementInfo, build_note, build_title, save_measurement

from .helpers import mock_data

QtGui = pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    yield app


@pytest.fixture(scope="module")
def big_data():
    t0 = time.perf_counter()
    d = mock_data(576_000)
    print(f"\n576,000 サンプル生成+解析+積算: {time.perf_counter() - t0:.1f} s")
    assert d.count == 576_000
    return d


def test_png_size_and_content(qapp, tmp_path):
    from ina226_monitor.png_export import image_to_png_bytes, render_image

    d = mock_data(2000)
    img = render_image(d, "REG-U3 / MAIN-RevB", build_note(d))
    assert (img.width(), img.height()) == (1600, 900)
    png = image_to_png_bytes(img)
    assert png.startswith(b"\x89PNG")
    loaded = QtGui.QImage.fromData(png)
    assert (loaded.width(), loaded.height()) == (1600, 900)


def test_png_empty_data(qapp):
    from ina226_monitor.measurement import MeasurementData
    from ina226_monitor.png_export import render_image

    img = render_image(MeasurementData(), "", "")
    assert img.width() == 1600


def test_decimate_keeps_peaks():
    x = np.arange(100_000, dtype=float)
    y = np.zeros_like(x)
    y[54_321] = 500.0
    y[77_777] = -20.0
    xd, yd = minmax_decimate(x, y, 2000)
    assert len(xd) <= 2000
    assert yd.max() == 500.0 and yd.min() == -20.0
    assert np.all(np.diff(xd) >= 0)


def test_visible_slice():
    x = np.arange(0, 100, 0.05)
    s = visible_slice(x, 40.0, 50.0)
    assert x[s][0] <= 40.0 and x[s][-1] >= 50.0
    assert len(x[s]) <= 203


def test_ac13_graph_update_under_half_second(big_data):
    x = big_data.column("elapsed_s")
    y = big_data.column("current_mA")
    t0 = time.perf_counter()
    for x0, x1 in [(0, x[-1]), (x[-1] - 60, x[-1]), (1000, 1300)]:
        s = visible_slice(x, x0, x1)
        minmax_decimate(x[s], y[s], 4000)
    assert time.perf_counter() - t0 < 0.5


def test_ac13_save_under_30_seconds(big_data, qapp, tmp_path):
    from ina226_monitor.png_export import image_to_png_bytes, render_image

    info = MeasurementInfo(target="REG-U3", memo="長時間")
    t0 = time.perf_counter()
    png = image_to_png_bytes(render_image(big_data, build_title(info), build_note(big_data)))
    r = save_measurement(tmp_path, "big", big_data, info, True, png)
    elapsed = time.perf_counter() - t0
    print(f"576,000 サンプル保存: {elapsed:.1f} s")
    assert elapsed < 30
    with open(r.paths[0], "rb") as f:
        assert sum(1 for _ in f) - 1 == 576_000  # 受信サンプル数と CSV 行数が一致


def test_100hz_8h_graph_and_save(qapp, tmp_path):
    """周期 10 ms × 8 時間（2,880,000 サンプル）でもグラフ更新 0.5 秒以内・保存 30 秒以内。"""
    from ina226_monitor.measurement import MeasurementData
    from ina226_monitor.png_export import image_to_png_bytes, render_image

    n = 2_880_000
    rng = np.random.default_rng(0)
    current = (25 + 10 * np.sin(np.arange(n) / 2000) + rng.normal(0, 0.05, n)).tolist()
    d = MeasurementData()
    t0 = 1_793_165_405.0
    for i, c in enumerate(current):
        d.add(i * 10, 3.7, c, t0 + i * 0.01)
    assert d.count == n

    x, y = d.column("elapsed_s"), d.column("current_mA")
    t = time.perf_counter()
    for x0, x1 in [(0, x[-1]), (x[-1] - 60, x[-1]), (10_000, 10_300)]:
        s = visible_slice(x, x0, x1)
        minmax_decimate(x[s], y[s], 4000)
    assert time.perf_counter() - t < 0.5

    info = MeasurementInfo(target="REG-U3", memo="100 Hz", interval_ms=10, avg=4)
    t = time.perf_counter()
    png = image_to_png_bytes(render_image(d, build_title(info), build_note(d, info)))
    r = save_measurement(tmp_path, "fast", d, info, True, png)
    elapsed = time.perf_counter() - t
    print(f"\n2,880,000 サンプル保存: {elapsed:.1f} s")
    assert elapsed < 30
    with open(r.paths[0], "rb") as f:
        assert sum(1 for _ in f) - 1 == n


def test_zero_based_range():
    """縦軸の下端は 0。マイナスの値があるときだけ下に広げる。"""
    assert zero_based_range(np.array([3.6, 3.7])) == pytest.approx((0.0, 3.7 * 1.05))
    lo, hi = zero_based_range(np.array([-0.2, 5.0]))
    assert lo < -0.2 and hi > 5.0
    lo, hi = zero_based_range(np.array([-0.125, -0.094]))  # 無負荷でオフセットがマイナス
    assert lo < -0.125 and hi == 0.0
    assert zero_based_range(np.array([0.0, 0.0])) == (0.0, 1.0)
    assert zero_based_range(np.array([])) == (0.0, 1.0)


def test_png_combined_mode(qapp):
    from ina226_monitor.png_export import render_image

    d = mock_data(500)
    img = render_image(d, "t", build_note(d), combined=True)
    assert (img.width(), img.height()) == (1600, 900)
    # 右側（電流の軸）に数値が描かれている＝右の余白が背景色だけではない
    right_margin = [img.pixelColor(x, y).name() for x in range(1480, 1560) for y in range(100, 780, 10)]
    assert len(set(right_margin)) > 1
