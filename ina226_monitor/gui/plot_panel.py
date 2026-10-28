"""リアルタイムグラフ（仕様 F-03）。上：電流 mA、下：電圧 V。横軸は共通。"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..decimate import minmax_decimate, visible_slice
from ..measurement import MeasurementData

CURRENT_COLOR = "#D9661F"  # オレンジ系
VOLTAGE_COLOR = "#3A3835"  # 黒系
RANGES = [("10 秒", 10.0), ("60 秒", 60.0), ("300 秒", 300.0), ("全体", None)]
DEFAULT_RANGE = 60.0
MAX_DRAW_POINTS = 4000


class PlotPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        pg.setConfigOptions(background="w", foreground="k", antialias=False)
        self._data: MeasurementData | None = None
        self._live = False  # 測定中は表示範囲を自動で追従させる
        self._window: float | None = DEFAULT_RANGE
        self._drawn_key = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("表示範囲："))
        self._range_group = QButtonGroup(self)
        self._range_group.setExclusive(True)
        self.range_buttons: dict[float | None, QPushButton] = {}
        for label, sec in RANGES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(sec == DEFAULT_RANGE)
            b.clicked.connect(lambda _=False, s=sec: self.set_window(s))
            self._range_group.addButton(b)
            self.range_buttons[sec] = b
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.glw = pg.GraphicsLayoutWidget()
        layout.addWidget(self.glw, 1)
        self.p_cur = self.glw.addPlot(row=0, col=0)
        self.p_vol = self.glw.addPlot(row=1, col=0)
        self.p_vol.setXLink(self.p_cur)
        for plot, label in ((self.p_cur, "電流 [mA]"), (self.p_vol, "電圧 [V]")):
            plot.showGrid(x=True, y=True, alpha=0.3)
            plot.setLabel("left", label)
            plot.getAxis("left").setWidth(70)
            plot.setMouseEnabled(x=True, y=True)
        self.p_vol.setLabel("bottom", "経過時間 [s]")
        self.c_cur = self.p_cur.plot(pen=pg.mkPen(CURRENT_COLOR, width=1.5))
        self.c_vol = self.p_vol.plot(pen=pg.mkPen(VOLTAGE_COLOR, width=1.5))

        # 停止後にユーザーが拡大・移動したら、見えている範囲を間引き直す
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(40)
        self._redraw_timer.timeout.connect(self._redraw_view)
        self.p_cur.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)

    # ---- 外部から ----
    def set_data(self, data: MeasurementData | None, live: bool) -> None:
        self._data = data
        self._live = live
        self._drawn_key = None
        self.refresh()

    def set_live(self, live: bool) -> None:
        self._live = live

    def set_window(self, seconds: float | None) -> None:
        self._window = seconds
        self.range_buttons[seconds].setChecked(True)
        self._drawn_key = None
        self.refresh()

    @property
    def window(self) -> float | None:
        return self._window

    def refresh(self) -> None:
        """表示範囲の設定に従って描き直す（測定中は 200 ms ごとに呼ばれる）。"""
        d = self._data
        if d is None or d.count == 0:
            self.c_cur.setData([], [])
            self.c_vol.setData([], [])
            return
        t_last = d.last_elapsed_s
        if self._window is None:
            x0, x1 = 0.0, max(t_last, 1.0)
        else:
            x1 = max(t_last, self._window)
            x0 = x1 - self._window
        key = (d.count, x0, x1)
        if key == self._drawn_key:
            return
        for plot in (self.p_cur, self.p_vol):
            plot.enableAutoRange(axis="y")
            plot.setAutoVisible(y=True)
        self._setting_range = True
        self.p_cur.setXRange(x0, x1, padding=0.0)
        self._setting_range = False
        self._draw(x0, x1)
        self._drawn_key = key

    # ---- 内部 ----
    _setting_range = False

    def _on_x_range_changed(self, *_):
        if not self._setting_range and not self._live:
            self._redraw_timer.start()

    def _redraw_view(self) -> None:
        if self._data is None or self._data.count == 0:
            return
        x0, x1 = self.p_cur.getViewBox().viewRange()[0]
        self._draw(x0, x1)
        self._drawn_key = None

    def _draw(self, x0: float, x1: float) -> None:
        d = self._data
        x = d.column("elapsed_s")
        s = visible_slice(x, x0, x1)
        xs = x[s]
        for curve, name in ((self.c_cur, "current_mA"), (self.c_vol, "bus_V")):
            xd, yd = minmax_decimate(xs, d.column(name)[s], MAX_DRAW_POINTS)
            curve.setData(xd, yd)
