"""GUI を模擬シリアルで動かす結合テスト（画面なし・offscreen）。"""

import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ina226_monitor.paths import AppPaths  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from ina226_monitor import theme

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def wait(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from ina226_monitor.gui import main_window as mw

    # メッセージボックスは自動で閉じる
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Ok))
    # 保存オプションのダイアログは出さずに、決めた値を返す
    out = tmp_path / "out"
    monkeypatch.setattr(mw.MainWindow, "_ask_save_options", lambda self, base, with_txt: (out, False))
    paths = AppPaths.under(tmp_path / "app")
    w = mw.MainWindow(paths, mock=True, mock_speed=10.0)
    w.show()
    yield w
    w.unsaved = False
    if w.state is mw.State.MEASURING:
        w._stop_measurement()
        w.unsaved = False
    w.close()


def select(w, device):
    w.port_combo.setCurrentIndex(w.port_combo.findData(device))


def test_measure_stop_save(window, qapp, tmp_path):
    from ina226_monitor.gui.main_window import State

    w = window
    select(w, "MOCK1")
    w.board_edit.setText("MAIN-RevB")
    w.target_edit.setText("REG-U3")
    w.operator_edit.setText("福井")
    assert w.datetime_edit.text() == ""
    assert w.start_button.isEnabled() and not w.stop_button.isEnabled() and not w.save_button.isEnabled()

    w.on_start()
    assert w.state is State.MEASURING
    assert w.datetime_edit.isReadOnly() and w.datetime_edit.text()
    assert w.name_edit.text().endswith("_REG-U3_MAIN-RevB_福井")
    assert not w.start_button.isEnabled() and w.stop_button.isEnabled() and not w.save_button.isEnabled()
    assert not w.zero_button.isEnabled() and not w.port_combo.isEnabled()
    assert w.measuring_badge.isVisible()

    wait(qapp, 2.0)  # 10 倍速なので約 20 秒分
    assert w.data.count > 100
    w._flush_tmp()
    wait(qapp, 0.3)
    assert list((tmp_path / "app" / "recovery").glob("*_tmp.csv"))

    w.on_stop()
    assert w.state is State.STOPPED and w.unsaved
    assert not w.measuring_badge.isVisible()
    assert w.plot.window is None  # 停止後は全体表示
    assert w.save_button.isEnabled() and w.start_button.isEnabled()
    n = w.data.count
    assert w.samples_label.text() == f"受信サンプル：{n:,}"

    w.memo_edit.setPlainText("メモ\n2 行目")
    assert w.on_save()
    out = tmp_path / "out"
    names = sorted(p.suffix for p in out.iterdir())
    assert names == [".csv", ".png", ".txt"]
    csv = next(out.glob("*.csv"))
    with open(csv, "rb") as f:
        assert sum(1 for _ in f) - 1 == n  # AC-08
    assert not w.unsaved
    assert list((tmp_path / "app" / "recovery").iterdir()) == []  # AC-17
    assert w.msg_label.text() == f"保存しました：{out}"


def test_manual_name_kept_on_start(window, qapp):
    w = window
    select(w, "MOCK1")
    w.name_edit.setText("my_name")
    w._on_name_edited("my_name")
    w.on_start()
    assert w.name_edit.text() == "my_name"  # AC-19
    w.on_stop()
    w.on_auto_name()
    assert w.name_edit.text() != "my_name"
    first = w.name_edit.text()
    w.unsaved = False
    time.sleep(1.1)
    w.on_start()
    assert w.name_edit.text() != first  # 書き換えていなければ作り直す
    w.on_stop()


def test_unsaved_prompt_cancel_keeps_window(window, qapp, monkeypatch):
    from ina226_monitor.gui.main_window import MainWindow

    w = window
    select(w, "MOCK1")
    w.on_start()
    wait(qapp, 0.5)
    w.on_stop()
    monkeypatch.setattr(MainWindow, "_ask_unsaved", lambda self: "cancel")
    assert not w.close()  # AC-12
    assert w.isVisible()
    monkeypatch.setattr(MainWindow, "_ask_unsaved", lambda self: "discard")
    assert w.close()
    assert w.writer is None


def test_disconnect_auto_stops(window, qapp, monkeypatch):
    from ina226_monitor import serial_io
    from ina226_monitor.gui.main_window import State
    from ina226_monitor.mock_serial import MockProfile

    monkeypatch.setitem(serial_io.MOCK_PORTS, "MOCK3", ("抜け", MockProfile(disconnect_after_s=8.0)))
    w = window
    select(w, "MOCK3")
    w.on_start()
    wait(qapp, 1.5)
    assert w.state is State.STOPPED  # AC-11
    assert w.msg_label.text() == "Arduino との接続が切れました"
    assert w.data.count > 10 and w.unsaved
    assert w.on_save()


def test_zero_cal_noload_then_load(window, qapp):
    w = window
    select(w, "MOCK2")
    w.on_zero_cal()
    assert w.offset_mA is not None and -0.2 < w.offset_mA < 0.0
    assert w.offset_label.text().startswith("ゼロ点補正値 -0.")
    before = w.offset_mA
    select(w, "MOCK1")
    w.on_zero_cal()  # 負荷あり → 補正値は変わらない
    assert w.offset_mA == before
    w.on_zero_reset()
    assert w.offset_label.text() == "ゼロ点補正値 なし"


def test_preload_8h(window, qapp, tmp_path):
    """AC-13：8 時間分を読み込んだ状態でのグラフ操作と保存。"""
    w = window
    w.load_mock_preload(576_000)
    t0 = time.perf_counter()
    for sec in (10.0, 60.0, 300.0, None):
        w.plot.set_window(sec)
        qapp.processEvents()
    w.plot.p_cur.getViewBox().setXRange(1000, 2000)
    wait(qapp, 0.1)
    assert (time.perf_counter() - t0) / 5 < 0.5
    t0 = time.perf_counter()
    assert w.on_save()
    assert time.perf_counter() - t0 < 30


def test_save_cancelled_in_options_dialog(window, qapp, monkeypatch, tmp_path):
    from ina226_monitor.gui.main_window import MainWindow

    w = window
    select(w, "MOCK1")
    w.on_start()
    wait(qapp, 0.5)
    w.on_stop()
    monkeypatch.setattr(MainWindow, "_ask_save_options", lambda self, base, with_txt: None)
    assert not w.on_save()
    assert w.unsaved
    assert not (tmp_path / "out").exists()


def test_layout_left_panel_fixed(window, qapp):
    """AC-25：広げても左パネルの幅は変わらず、グラフが広がる。最小サイズでも操作部品が見える。"""
    from PySide6.QtWidgets import QScrollArea

    from ina226_monitor.gui.main_window import LEFT_PANEL_WIDTH

    w = window
    scroll = w.findChild(QScrollArea, "leftScroll")
    w.resize(1920, 1080)
    wait(qapp, 0.1)
    assert scroll.width() == LEFT_PANEL_WIDTH
    big = w.plot.width()
    w.resize(1024, 700)
    wait(qapp, 0.1)
    assert scroll.width() == LEFT_PANEL_WIDTH
    assert w.plot.width() < big
    for b in (w.start_button, w.stop_button, w.save_button, w.refresh_button, w.zero_button, w.auto_name_button):
        assert b.isVisible()
        r = b.mapTo(w, b.rect().bottomRight())
        assert r.x() <= w.width() and r.y() <= w.height()


def test_theme_stylesheet_and_icon(qapp):
    from PySide6.QtGui import QIcon

    from ina226_monitor import theme
    from ina226_monitor.resources import APP_ICON, resource_path

    import re
    from pathlib import Path

    qss = theme.stylesheet()
    assert "$" not in qss
    for url in re.findall(r"url\(([^)]+)\)", qss):
        assert Path(url).is_file(), url
    assert "#A3282C" in qss and "rgba(213, 120, 53, 102)" in qss  # 開始ボタンの無効時 = 40%
    icon = QIcon(str(resource_path(APP_ICON)))
    assert not icon.isNull()
    assert (256, 256) in [(s.width(), s.height()) for s in icon.availableSizes()]


def test_zero_reset_from_context_menu(window, qapp):
    w = window
    w.offset_mA = -0.1
    w._update_offset_label()
    assert w.offset_label.text() == "ゼロ点補正値 -0.100 mA"
    w.on_zero_reset()
    assert w.offset_label.text() == "ゼロ点補正値 なし"
