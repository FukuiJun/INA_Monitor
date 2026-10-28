"""リアルタイムグラフ（仕様 F-03・7.1）。上：電流 mA、下：電圧 V。横軸は共通。"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..decimate import minmax_decimate, visible_slice
from ..measurement import MeasurementData
from ..theme import COLORS, CURRENT_LINE_WIDTH, VOLTAGE_LINE_WIDTH, mono_font, ui_font

RANGES = [("10秒", 10.0), ("60秒", 60.0), ("300秒", 300.0), ("全体", None)]
DEFAULT_RANGE = 60.0
MAX_DRAW_POINTS = 4000
LEFT_AXIS_WIDTH = 52


class SecondsAxis(pg.AxisItem):
    """横軸。いちばん右の目盛りにだけ単位 s を付ける。"""

    def tickStrings(self, values, scale, spacing):
        strs = super().tickStrings(values, scale, spacing)
        if strs:
            strs[-1] = f"{strs[-1]} s"
        return strs


def _make_plot(bottom_axis: pg.AxisItem | None = None) -> pg.PlotWidget:
    axis_items = {"bottom": bottom_axis} if bottom_axis is not None else None
    pw = pg.PlotWidget(background=COLORS["panel_bg"], axisItems=axis_items)
    pi = pw.getPlotItem()
    pi.hideButtons()
    pi.setMenuEnabled(False)
    pi.layout.setContentsMargins(0, 0, 0, 0)
    vb = pi.getViewBox()
    vb.setBackgroundColor(COLORS["graph_bg"])
    vb.setBorder(pg.mkPen(COLORS["graph_grid"]))
    tick_font = mono_font(11)
    for name in ("left", "bottom"):
        ax = pi.getAxis(name)
        ax.setPen(pg.mkPen(COLORS["graph_grid"]))
        ax.setTextPen(pg.mkPen(COLORS["graph_axis"]))
        ax.setStyle(tickFont=tick_font, tickLength=0, maxTickLevel=0)  # 主目盛りだけ
        ax.setTickDensity(0.6)
    pi.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
    pi.showGrid(x=False, y=True, alpha=1.0)  # モックに合わせて横線だけ
    return pw


class PlotPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        pg.setConfigOptions(antialias=False)  # 太い線でアンチエイリアスをかけると描画が極端に遅くなる
        self._data: MeasurementData | None = None
        self._live = False  # 測定中は表示範囲を自動で追従させる
        self._window: float | None = DEFAULT_RANGE
        self._drawn_key = None
        self._setting_range = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        cur_label = QLabel("電流 [mA]")
        cur_label.setObjectName("currentAxisLabel")
        cur_label.setFont(ui_font(12, bold=True))
        top.addWidget(cur_label)
        top.addStretch(1)
        group = QFrame()
        group.setObjectName("rangeGroup")
        gl = QHBoxLayout(group)
        gl.setContentsMargins(1, 1, 1, 1)
        gl.setSpacing(0)
        self._range_group = QButtonGroup(self)
        self._range_group.setExclusive(True)
        self.range_buttons: dict[float | None, QPushButton] = {}
        for i, (label, sec) in enumerate(RANGES):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(sec == DEFAULT_RANGE)
            b.setFont(ui_font(13))
            if i == 0:
                b.setProperty("pos", "first")
            elif i == len(RANGES) - 1:
                b.setProperty("pos", "last")
            b.clicked.connect(lambda _=False, s=sec: self.set_window(s))
            self._range_group.addButton(b)
            self.range_buttons[sec] = b
            gl.addWidget(b)
        top.addWidget(group)
        layout.addLayout(top)

        self.pw_cur = _make_plot()
        self.pw_vol = _make_plot(SecondsAxis("bottom"))
        self.p_cur = self.pw_cur.getPlotItem()
        self.p_vol = self.pw_vol.getPlotItem()
        self.p_cur.getAxis("bottom").setStyle(showValues=False)
        self.p_cur.getAxis("bottom").setHeight(1)
        self.p_vol.setXLink(self.p_cur)
        layout.addWidget(self.pw_cur, 3)  # 電流グラフは電圧グラフの約 1.5 倍の高さ

        vol_label = QLabel("電圧 [V]")
        vol_label.setObjectName("voltageAxisLabel")
        vol_label.setFont(ui_font(12, bold=True))
        layout.addWidget(vol_label)
        layout.addWidget(self.pw_vol, 2)

        self.c_cur = self.p_cur.plot(pen=pg.mkPen(COLORS["current_line"], width=CURRENT_LINE_WIDTH))
        self.c_vol = self.p_vol.plot(pen=pg.mkPen(COLORS["voltage_line"], width=VOLTAGE_LINE_WIDTH))

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
