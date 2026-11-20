"""リアルタイムグラフ（仕様 F-03・7.1）。SDL_DischargeLogger の液晶スタイルのグラフにならう。

- 黒い背景に縦横のグリッド、目盛りの数字は等幅
- 縦軸：下端 0（マイナスがあれば下の区切り）、上端は区切りのよい値。測定中は広げる方向にだけ変える
- 横軸：区切りのよい時間の目盛り（m:ss または h:mm）。最後の目盛りに単位（分／h）を付ける
  - 「全体」：測定中は経過時間の 1.2 倍まで先に取る（軸が頻繁に変わらない）。停止後は測定時間に合わせる
  - 5 秒〜3 時間：測定中はページ送り（右端に着いたら次の時間幅を左端から）。時間幅を変えたら最新の値を中央に
- 表示モード：2 段（上に電流 mA・下に電圧 V）と、重ねる（左軸 電圧・右軸 電流）
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QRectF, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..axes import format_time_tick, format_y_ticks, full_time_range, nice_axis, time_step, time_ticks, time_unit
from ..decimate import minmax_decimate, visible_slice
from ..measurement import MeasurementData
from ..theme import COLORS, CURRENT_LINE_WIDTH, VOLTAGE_LINE_WIDTH, mono_font, ui_font

RANGES = [  # 表示範囲の切り替え（ラベル, 時間幅［秒］。None は全体）
    ("5秒", 5.0),
    ("30秒", 30.0),
    ("5分", 300.0),
    ("30分", 1800.0),
    ("3時間", 10800.0),
    ("全体", None),
]
DEFAULT_RANGE = None  # 初期表示は「全体」
MAX_DRAW_POINTS = 4000
LEFT_AXIS_WIDTH = 62  # 縦軸の数値欄の幅の初期値（描画のたびに数値に合わせて詰める）
AXIS_MIN_WIDTH = 24
AXIS_TEXT_GAP = 9  # 数値と描画領域のあいだ（pyqtgraph の tickTextOffset 5px + 余裕）
TICK_FONT_PX = 13  # 目盛りの数値の文字の大きさ
Y_TICK_MIN_GAP_PX = 32  # 縦軸の目盛りの最小の間隔（グラフが低いときは区切りを減らす）

MODE_SPLIT = "split"
MODE_COMBINED = "combined"
MODES = [("2段", MODE_SPLIT), ("重ねる", MODE_COMBINED)]


class LcdAxis(pg.AxisItem):
    """軸の端ちょうどの目盛りの数字も表示し、枠からはみ出す分は内側へずらす（SDL と同じく 0:00 は左寄せ・最後は右寄せ）。

    pyqtgraph は目盛線（グリッド）を出していると、端の数字を描かずに捨ててしまうため。
    """

    EDGE_PX = 48

    def boundingRect(self):
        r = super().boundingRect()
        if self.orientation in ("bottom", "top"):
            return r.adjusted(-self.EDGE_PX, 0, self.EDGE_PX, 0)
        return r.adjusted(0, -self.EDGE_PX, 0, self.EDGE_PX)

    def generateDrawSpecs(self, p):
        specs = super().generateDrawSpecs(p)
        if specs is None:
            return specs
        axis_spec, tick_specs, text_specs = specs
        b = self.mapRectFromParent(self.geometry())
        fixed = []
        for rect, flags, text in text_specs:
            r = QRectF(rect)
            if self.orientation in ("bottom", "top"):
                if r.left() < b.left():
                    r.moveLeft(b.left())
                if r.right() > b.right():
                    r.moveRight(b.right())
            else:
                if r.top() < b.top():
                    r.moveTop(b.top())
                if r.bottom() > b.bottom():
                    r.moveBottom(b.bottom())
            fixed.append((r, flags, text))
        return axis_spec, tick_specs, fixed


def _style_axis(ax: pg.AxisItem, text_color: str) -> None:
    ax.setPen(pg.mkPen(COLORS["lcd_grid"]))  # 目盛線（グリッド）の色
    ax.setTextPen(pg.mkPen(text_color))
    ax.setStyle(tickFont=mono_font(TICK_FONT_PX), tickLength=0, maxTickLevel=0)


def _make_plot() -> pg.PlotWidget:
    axis_items = {name: LcdAxis(name) for name in ("left", "bottom", "right")}
    pw = pg.PlotWidget(background=COLORS["lcd_bg"], axisItems=axis_items)
    # pyqtgraph の既定（変わった所だけ描き直す）では、Windows の表示倍率によって
    # 横一直線の波形（一定の電圧など）の一部が描かれずに残ることがあるので、毎回全体を描き直す
    pw.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    pi = pw.getPlotItem()
    pi.hideButtons()
    pi.setMenuEnabled(False)
    pi.layout.setContentsMargins(0, 0, 0, 0)
    vb = pi.getViewBox()
    vb.setBackgroundColor(COLORS["lcd_bg"])
    vb.setBorder(pg.mkPen(COLORS["lcd_frame"]))
    for name in ("left", "bottom", "right"):
        _style_axis(pi.getAxis(name), COLORS["lcd_tick"])
    pi.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
    pi.hideAxis("right")  # 右の軸は「重ねる」表示（電流）のときだけ出す
    pi.showGrid(x=True, y=True, alpha=1.0)  # 縦横のグリッド
    return pw


def _segmented(items, font_px: int = 13) -> tuple[QFrame, QButtonGroup, list[QPushButton]]:
    """ひとつながりの切り替えボタン（液晶の上では、選択中は灰色の地に黄色の文字）。"""
    group = QFrame()
    group.setObjectName("rangeGroup")
    gl = QHBoxLayout(group)
    gl.setContentsMargins(1, 1, 1, 1)
    gl.setSpacing(0)
    bg = QButtonGroup(group)
    bg.setExclusive(True)
    buttons = []
    for i, label in enumerate(items):
        b = QPushButton(label)
        b.setCheckable(True)
        b.setFont(ui_font(font_px))
        b.setMinimumWidth(b.sizeHint().width())  # 狭いときもボタンの文字は削らない（見出しのほうを詰める）
        if i == 0:
            b.setProperty("pos", "first")
        elif i == len(items) - 1:
            b.setProperty("pos", "last")
        bg.addButton(b)
        gl.addWidget(b)
        buttons.append(b)
    return group, bg, buttons


class PlotPanel(QWidget):
    """液晶パネルの中に置くグラフ部分（見出し・切り替えボタン・グラフ）。"""

    mode_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("lcdGraph")
        pg.setConfigOptions(antialias=False)  # 太い線でアンチエイリアスをかけると描画が極端に遅くなる
        self._data: MeasurementData | None = None
        self._live = False  # 測定中は表示範囲を自動で追従させる
        self._window: float | None = DEFAULT_RANGE
        self._mode = MODE_SPLIT
        self._drawn_key = None
        self._setting_range = False
        self._page_start = 0.0  # 測定中の表示の左端（経過秒）
        self._y_extent: dict[str, tuple[float, float]] = {}  # 測定中に見えたデータの最小・最大（縦軸を縮めないため）
        self._y_labels: dict[str, list[str]] = {}  # 縦軸の目盛りの文字（数値欄の幅を決める）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1 行目：グラフ名（左）、表示モードと表示範囲の切り替え（右）
        top = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName("currentAxisLabel")
        # 見出しは縦軸の数値欄の右、グラフの描画領域の左端にそろえる
        self.title_label.setContentsMargins(LEFT_AXIS_WIDTH, 0, 0, 0)
        self.title_label.setFont(ui_font(12, bold=True))
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # 狭いときは見出しを詰める
        self.title_label.setMinimumWidth(0)
        top.addWidget(self.title_label, 1)
        mode_group, _, mode_buttons = _segmented([label for label, _ in MODES], 12)
        self.mode_buttons: dict[str, QPushButton] = {}
        for b, (_, mode) in zip(mode_buttons, MODES):
            b.setToolTip("2段：上に電流・下に電圧／重ねる：左軸に電圧・右軸に電流")
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_buttons[mode] = b
        top.addWidget(mode_group)
        top.addSpacing(8)
        range_group, _, range_buttons = _segmented([label for label, _ in RANGES], 12)
        self.range_buttons: dict[float | None, QPushButton] = {}
        for b, (_, sec) in zip(range_buttons, RANGES):
            b.clicked.connect(lambda _=False, s=sec: self.set_window(s))
            self.range_buttons[sec] = b
        top.addWidget(range_group)
        layout.addLayout(top)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # ---- 2 段 ----
        split = QWidget()
        sl = QVBoxLayout(split)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        self.pw_cur = _make_plot()
        self.pw_vol = _make_plot()
        self.p_cur = self.pw_cur.getPlotItem()
        self.p_vol = self.pw_vol.getPlotItem()
        self.p_cur.getAxis("bottom").setStyle(showValues=False)  # 時間の数字は下のグラフにだけ
        self.p_cur.getAxis("bottom").setHeight(1)
        self.p_vol.setXLink(self.p_cur)
        sl.addWidget(self.pw_cur, 3)  # 電流グラフは電圧グラフの約 1.5 倍の高さ
        vol_label = QLabel("電圧 [V]")
        vol_label.setObjectName("voltageAxisLabel")
        vol_label.setContentsMargins(LEFT_AXIS_WIDTH, 0, 0, 0)
        vol_label.setFont(ui_font(12, bold=True))
        self.vol_label = vol_label
        sl.addWidget(vol_label)
        sl.addWidget(self.pw_vol, 2)
        self.stack.addWidget(split)
        self.c_cur = self.p_cur.plot(pen=pg.mkPen(COLORS["current_line"], width=CURRENT_LINE_WIDTH))
        self.c_vol = self.p_vol.plot(pen=pg.mkPen(COLORS["voltage_line"], width=VOLTAGE_LINE_WIDTH))

        # ---- 重ねる：左軸 電圧（PlotItem 本体）、右軸 電流（重ねた ViewBox） ----
        self.pw_comb = _make_plot()
        self.p_comb = self.pw_comb.getPlotItem()
        self.vb_comb_cur = pg.ViewBox()
        self.p_comb.showAxis("right")
        right = self.p_comb.getAxis("right")
        _style_axis(right, COLORS["current_line"])
        right.setWidth(LEFT_AXIS_WIDTH)
        right.linkToView(self.vb_comb_cur)
        self.p_comb.scene().addItem(self.vb_comb_cur)
        # 電圧側（PlotItem 内の ViewBox）は背景色で塗りつぶすので、電流側を PlotItem ごと上に重ねる
        self.vb_comb_cur.setZValue(self.p_comb.zValue() + 1)
        self.vb_comb_cur.setXLink(self.p_comb)
        self.p_comb.getViewBox().sigResized.connect(self._sync_comb_views)
        self.p_comb.getAxis("left").setTextPen(pg.mkPen(COLORS["voltage_line"]))
        self.c_comb_vol = self.p_comb.plot(pen=pg.mkPen(COLORS["voltage_line"], width=VOLTAGE_LINE_WIDTH))
        self.c_comb_cur = pg.PlotCurveItem(pen=pg.mkPen(COLORS["current_line"], width=CURRENT_LINE_WIDTH))
        self.vb_comb_cur.addItem(self.c_comb_cur)
        self.stack.addWidget(self.pw_comb)

        # 停止後にユーザーが拡大・移動したら、見えている範囲を間引き直す
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(40)
        self._redraw_timer.timeout.connect(self._redraw_view)
        self.p_cur.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)
        self.p_comb.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)
        # 大きさが変わったら縦軸の目盛りの数を合わせ直す
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._redraw_after_resize)
        for vb in (self.p_cur.getViewBox(), self.p_vol.getViewBox(), self.p_comb.getViewBox()):
            vb.sigResized.connect(lambda *_: self._resize_timer.start())

        self.range_buttons[DEFAULT_RANGE].setChecked(True)
        self._apply_mode_widgets()
        self._draw_empty()

    # ---- 外部から ----
    def set_data(self, data: MeasurementData | None, live: bool) -> None:
        self._data = data
        self._live = live
        self._page_start = 0.0
        self._y_extent.clear()
        self._drawn_key = None
        self.refresh()

    def set_live(self, live: bool) -> None:
        self._live = live
        self._y_extent.clear()  # 停止後は縦軸を見えているデータに合わせて詰め直す

    def set_window(self, seconds: float | None) -> None:
        self._window = seconds
        self.range_buttons[seconds].setChecked(True)
        if self._live and seconds is not None:
            # 測定中に時間幅を変えたら、最新の値が画面の中央に来るようにして、そこから右へ描き進める
            t_last = self._data.last_elapsed_s if self._data is not None and self._data.count else 0.0
            self._page_start = max(0.0, t_last - seconds / 2)
        self._y_extent.clear()
        self._drawn_key = None
        self.refresh()

    @property
    def window(self) -> float | None:
        return self._window

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_SPLIT, MODE_COMBINED):
            mode = MODE_SPLIT
        changed = mode != self._mode
        self._mode = mode
        self._apply_mode_widgets()
        self._y_extent.clear()
        self._drawn_key = None
        self.refresh()
        if changed:
            self.mode_changed.emit(mode)

    @property
    def mode(self) -> str:
        return self._mode

    def refresh(self) -> None:
        """表示範囲の設定に従って描き直す（測定中は 200 ms ごとに呼ばれる）。"""
        d = self._data
        if d is None or d.count == 0:
            self._draw_empty()
            return
        t_last = d.last_elapsed_s
        if self._window is None:
            x0, x1, step = full_time_range(t_last, live=self._live)
        elif self._live:
            # ページ送り：波形が右端に着いたら、次の時間幅に切り替えて左端から描き直す
            while t_last >= self._page_start + self._window:
                self._page_start += self._window
            x0, x1 = self._page_start, self._page_start + self._window
            step = time_step(self._window)
        else:
            # 停止後は最後の値が右端に来るように表示する
            x1 = max(t_last, self._window)
            x0 = x1 - self._window
            step = time_step(self._window)
        key = (self._mode, d.count, x0, x1)
        if key == self._drawn_key:
            return
        self._set_x(x0, x1, step)
        self._draw(x0, x1)
        self._drawn_key = key

    # ---- 内部 ----
    def _x_master(self) -> pg.PlotItem:
        return self.p_comb if self._mode == MODE_COMBINED else self.p_cur

    def _apply_mode_widgets(self) -> None:
        combined = self._mode == MODE_COMBINED
        self.mode_buttons[self._mode].setChecked(True)
        self.stack.setCurrentIndex(1 if combined else 0)
        if combined:
            vol, cur = COLORS["voltage_line"], COLORS["current_line"]
            self.title_label.setText(
                f"<span style='color:{vol}'>電圧 [V] 左軸</span>"
                f"<span style='color:{COLORS['lcd_label']}'> ／ </span>"
                f"<span style='color:{cur}'>電流 [mA] 右軸</span>"
            )
        else:
            self.title_label.setText("電流 [mA]")

    def _sync_comb_views(self) -> None:
        vb = self.p_comb.getViewBox()
        self.vb_comb_cur.setGeometry(vb.sceneBoundingRect())
        self.vb_comb_cur.linkedViewChanged(vb, self.vb_comb_cur.XAxis)

    def _on_x_range_changed(self, *_):
        if not self._setting_range and not self._live:
            self._redraw_timer.start()

    def _redraw_view(self) -> None:
        if self._data is None or self._data.count == 0:
            return
        x0, x1 = self._x_master().getViewBox().viewRange()[0]
        self._set_ticks_x(x0, x1, time_step(max(x1 - x0, 1e-6)))
        self._draw(x0, x1)
        self._drawn_key = None

    def _redraw_after_resize(self) -> None:
        self._drawn_key = None
        self.refresh()

    def _draw_empty(self) -> None:
        for curve in (self.c_cur, self.c_vol, self.c_comb_cur, self.c_comb_vol):
            curve.setData([], [])
        x0, x1, step = full_time_range(0.0, live=True)
        self._set_x(x0, x1, step)
        for key, vb, axis in self._y_targets():
            self._set_y(key, vb, axis, 0.0, 1.0)
        self._fit_axis_widths()

    def _set_x(self, x0: float, x1: float, step: float) -> None:
        self._setting_range = True
        self._x_master().setXRange(x0, x1, padding=0.0)
        self._setting_range = False
        self._set_ticks_x(x0, x1, step)

    def _set_ticks_x(self, x0: float, x1: float, step: float) -> None:
        values, step = time_ticks(x0, x1, step)
        labels = [format_time_tick(v, step) for v in values]
        if labels:
            labels[-1] = f"{labels[-1]} {time_unit(step)}"
        ticks = [list(zip(values, labels))]
        if self._mode == MODE_COMBINED:
            self.p_comb.getAxis("bottom").setTicks(ticks)
        else:
            self.p_cur.getAxis("bottom").setTicks([[(v, "") for v in values]])  # 縦のグリッド線だけ
            self.p_vol.getAxis("bottom").setTicks(ticks)

    def _y_targets(self):
        """(名前, ViewBox, 縦軸) の組。名前は縦軸の範囲を覚えておくためのもの。"""
        if self._mode == MODE_COMBINED:
            return (("cur", self.vb_comb_cur, self.p_comb.getAxis("right")),
                    ("vol", self.p_comb.getViewBox(), self.p_comb.getAxis("left")))
        return (("cur", self.p_cur.getViewBox(), self.p_cur.getAxis("left")),
                ("vol", self.p_vol.getViewBox(), self.p_vol.getAxis("left")))

    def _set_y(self, key: str, vb: pg.ViewBox, axis: pg.AxisItem, ymin: float, ymax: float) -> None:
        if self._live and key in self._y_extent:
            # 測定中は広げる方向にだけ変える（波形が上下に動いて見えないように）
            pmin, pmax = self._y_extent[key]
            ymin, ymax = min(ymin, pmin), max(ymax, pmax)
        if self._live:
            self._y_extent[key] = (ymin, ymax)
        divisions = max(2, min(5, int(vb.height() // Y_TICK_MIN_GAP_PX))) if vb.height() > 0 else 5
        lo, hi, ticks = nice_axis(ymin, ymax, divisions)
        labels = format_y_ticks(ticks)
        vb.setYRange(lo, hi, padding=0.0)
        axis.setTicks([list(zip(ticks, labels))])
        self._y_labels[key] = labels

    def _draw(self, x0: float, x1: float) -> None:
        d = self._data
        x = d.column("elapsed_s")
        s = visible_slice(x, x0, x1)
        xs = x[s]
        series = {"cur": d.column("current_mA")[s], "vol": d.column("bus_V")[s]}
        curves = ({"cur": self.c_comb_cur, "vol": self.c_comb_vol} if self._mode == MODE_COMBINED
                  else {"cur": self.c_cur, "vol": self.c_vol})
        for key, vb, axis in self._y_targets():
            y = series[key]
            xd, yd = minmax_decimate(xs, y, MAX_DRAW_POINTS)
            curves[key].setData(xd, yd)
            if len(y):
                self._set_y(key, vb, axis, float(y.min()), float(y.max()))
            else:
                self._set_y(key, vb, axis, 0.0, 0.0)
        self._fit_axis_widths()

    def _text_width(self, key: str) -> int:
        fm = QFontMetrics(mono_font(TICK_FONT_PX))
        return max((fm.horizontalAdvance(t) for t in self._y_labels.get(key, [])), default=0)

    def _fit_axis_widths(self) -> None:
        """縦軸の数値欄を数値の長さに合わせて詰め、左側の無駄な余白をなくす。

        2 段表示では上下のグラフの左端がそろうよう、2 つの縦軸を同じ幅にする。
        見出しも描画領域の左端にそろえる。
        """
        def width(text_w: int) -> int:
            return max(AXIS_MIN_WIDTH, text_w + AXIS_TEXT_GAP)

        if self._mode == MODE_COMBINED:
            left, right = width(self._text_width("vol")), width(self._text_width("cur"))
            axes = ((self.p_comb.getAxis("left"), left), (self.p_comb.getAxis("right"), right))
        else:
            left = width(max(self._text_width("cur"), self._text_width("vol")))
            axes = ((self.p_cur.getAxis("left"), left), (self.p_vol.getAxis("left"), left))
        for ax, w in axes:
            if ax.width() != w:
                ax.setWidth(w)
        for label in (self.title_label, self.vol_label):
            if label.contentsMargins().left() != left:
                label.setContentsMargins(left, 0, 0, 0)
