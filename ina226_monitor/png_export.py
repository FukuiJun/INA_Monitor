"""保存用グラフ画像（PNG 1600×900）。仕様 5.2。画面と同じ表示モード（2 段／まとめて表示）で描く。

QPainter で QImage に直接描く（画面のウィジェットは使わない）。QGuiApplication が必要。
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .decimate import minmax_decimate, zero_based_range
from .measurement import MeasurementData
from .theme import COLORS, CURRENT_LINE_WIDTH, VOLTAGE_LINE_WIDTH, mono_font, ui_font

WIDTH, HEIGHT = 1600, 900


def nice_ticks(lo: float, hi: float, target: int = 6) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step - 1e-9) * step
    ticks = []
    v = first
    while v <= hi + step * 1e-9:
        ticks.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
    return ticks


def _fmt_tick(v: float, ticks: list[float]) -> str:
    step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else 1.0
    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    if abs(round(step, decimals) - step) > step * 1e-6:  # 2.5 刻みなど
        decimals += 1
    return f"{v:.{decimals}f}"


def _px(rect: QRectF, x_range: tuple[float, float]):
    x0, x1 = x_range
    return lambda v: rect.left() + (v - x0) / (x1 - x0) * rect.width()


def _py(rect: QRectF, y_range: tuple[float, float]):
    y0, y1 = y_range
    return lambda v: rect.bottom() - (v - y0) / (y1 - y0) * rect.height()


def _draw_frame(p: QPainter, rect: QRectF, x_range: tuple[float, float], show_x_labels: bool) -> None:
    """背景・縦の目盛線・横軸の数値。"""
    p.fillRect(rect, QColor(COLORS["graph_bg"]))
    xticks = nice_ticks(*x_range, 10)
    px = _px(rect, x_range)
    p.setFont(mono_font(16))
    for t in xticks:
        p.setPen(QPen(QColor(COLORS["graph_grid"]), 1))
        p.drawLine(QPointF(px(t), rect.top()), QPointF(px(t), rect.bottom()))
        if show_x_labels:
            p.setPen(QColor(COLORS["graph_axis"]))
            p.drawText(QRectF(px(t) - 60, rect.bottom() + 6, 120, 22), Qt.AlignHCenter | Qt.AlignTop, _fmt_tick(t, xticks))


def _draw_y_axis(
    p: QPainter,
    rect: QRectF,
    y_range: tuple[float, float],
    side: str,
    label: str,
    label_color: str,
    tick_color: str,
    grid: bool,
) -> None:
    """縦軸の目盛り（数値・横の目盛線）とラベル。side は "left" か "right"。"""
    yticks = nice_ticks(*y_range, 6)
    py = _py(rect, y_range)
    p.setFont(mono_font(16))
    for t in yticks:
        if grid:
            p.setPen(QPen(QColor(COLORS["graph_grid"]), 1))
            p.drawLine(QPointF(rect.left(), py(t)), QPointF(rect.right(), py(t)))
        p.setPen(QColor(tick_color))
        if side == "left":
            box, align = QRectF(rect.left() - 100, py(t) - 11, 92, 22), Qt.AlignRight | Qt.AlignVCenter
        else:
            box, align = QRectF(rect.right() + 8, py(t) - 11, 92, 22), Qt.AlignLeft | Qt.AlignVCenter
        p.drawText(box, align, _fmt_tick(t, yticks))
    p.save()
    p.setPen(QColor(label_color))
    p.setFont(ui_font(18, bold=True))
    if side == "left":
        p.translate(rect.left() - 92, rect.center().y())
        p.rotate(-90)
    else:
        p.translate(rect.right() + 92, rect.center().y())
        p.rotate(90)
    p.drawText(QRectF(-rect.height() / 2, -24, rect.height(), 28), Qt.AlignCenter, label)
    p.restore()


def _draw_series(
    p: QPainter,
    rect: QRectF,
    x: np.ndarray,
    y: np.ndarray,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    color: str,
    width: float,
) -> None:
    if not len(x):
        return
    px, py = _px(rect, x_range), _py(rect, y_range)
    xd, yd = minmax_decimate(x, y, int(rect.width()) * 2)
    pts = [QPointF(a, b) for a, b in zip(map(px, xd.tolist()), map(py, yd.tolist()))]
    p.save()
    p.setClipRect(rect)
    p.setPen(QPen(QColor(color), width))
    # drawPolyline は太さのある線の継ぎ目計算が遅いので、線分の集まりとして描く
    p.drawLines([QLineF(a, b) for a, b in zip(pts, pts[1:])])
    p.restore()


def _finish_plot(p: QPainter, rect: QRectF, has_data: bool) -> None:
    if not has_data:
        p.setPen(QColor(COLORS["graph_axis"]))
        p.drawText(rect, Qt.AlignCenter, "データなし")
    p.setPen(QPen(QColor(COLORS["graph_grid"]), 1))
    p.drawRect(rect)


def render_image(data: MeasurementData, title: str, note: str, combined: bool = False) -> QImage:
    """combined=False：上段に電流・下段に電圧。True：1 つのグラフに左軸 電圧・右軸 電流。"""
    img = QImage(WIDTH, HEIGHT, QImage.Format_RGB32)
    img.fill(QColor(COLORS["panel_bg"]))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    text = QColor(COLORS["text"])
    p.setPen(text)
    p.setFont(ui_font(26, bold=True))
    p.drawText(QRectF(0, 14, WIDTH, 40), Qt.AlignCenter, title)

    x = data.column("elapsed_s")
    cur = data.column("current_mA")
    vol = data.column("bus_V")
    x_end = float(x[-1]) if len(x) else 1.0
    x_range = (0.0, x_end if x_end > 0 else 1.0)
    has_data = len(x) > 0
    # 縦軸の下端は 0（マイナスの値があるときだけ下に広げる）
    cur_range, vol_range = zero_based_range(cur), zero_based_range(vol)
    top, bottom = 72, 120

    if combined:
        left, right = 130, 130
        rect = QRectF(left, top, WIDTH - left - right, HEIGHT - top - bottom)
        _draw_frame(p, rect, x_range, True)
        _draw_y_axis(p, rect, vol_range, "left", "電圧 [V]", COLORS["voltage_label"], COLORS["graph_axis"], True)
        _draw_y_axis(p, rect, cur_range, "right", "電流 [mA]", COLORS["current_label"], COLORS["current_label"], False)
        _draw_series(p, rect, x, vol, x_range, vol_range, COLORS["voltage_line"], VOLTAGE_LINE_WIDTH)
        _draw_series(p, rect, x, cur, x_range, cur_range, COLORS["current_line"], CURRENT_LINE_WIDTH)
        _finish_plot(p, rect, has_data)
        x_label_top = rect.bottom()
    else:
        left, right = 130, 40
        gap = 30
        plot_h = (HEIGHT - top - bottom - gap) / 2
        r_cur = QRectF(left, top, WIDTH - left - right, plot_h)
        r_vol = QRectF(left, top + plot_h + gap, WIDTH - left - right, plot_h)
        for rect, y, y_range, label, label_color, color, width, show_x in (
            (r_cur, cur, cur_range, "電流 [mA]", COLORS["current_label"], COLORS["current_line"], CURRENT_LINE_WIDTH, False),
            (r_vol, vol, vol_range, "電圧 [V]", COLORS["voltage_label"], COLORS["voltage_line"], VOLTAGE_LINE_WIDTH, True),
        ):
            _draw_frame(p, rect, x_range, show_x)
            _draw_y_axis(p, rect, y_range, "left", label, label_color, COLORS["graph_axis"], True)
            _draw_series(p, rect, x, y, x_range, y_range, color, width)
            _finish_plot(p, rect, has_data)
        x_label_top = r_vol.bottom()

    p.setPen(QColor(COLORS["text_sub"]))
    p.setFont(ui_font(18))
    p.drawText(QRectF(0, x_label_top + 28, WIDTH, 26), Qt.AlignCenter, "経過時間 [s]")
    p.setPen(text)
    p.setFont(ui_font(20))
    p.drawText(QRectF(0, HEIGHT - 44, WIDTH, 32), Qt.AlignCenter, note)
    p.end()
    return img


def image_to_png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    if not img.save(buf, "PNG"):
        raise OSError("PNG の作成に失敗しました")
    buf.close()
    return bytes(ba.data())
