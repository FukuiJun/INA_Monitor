"""保存用グラフ画像（PNG 1600×900）。仕様 5.2。

QPainter で QImage に直接描く（画面のウィジェットは使わない）。QGuiApplication が必要。
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .decimate import minmax_decimate
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


def _y_range(y: np.ndarray) -> tuple[float, float]:
    if len(y) == 0:
        return 0.0, 1.0
    lo, hi = float(np.min(y)), float(np.max(y))
    if hi - lo < 1e-9:
        pad = max(abs(hi) * 0.05, 0.5)
        return lo - pad, hi + pad
    pad = (hi - lo) * 0.05
    return lo - pad, hi + pad


def _draw_plot(
    p: QPainter,
    rect: QRectF,
    x: np.ndarray,
    y: np.ndarray,
    x_range: tuple[float, float],
    label: str,
    label_color: str,
    color: str,
    width: float,
    show_x_labels: bool,
) -> None:
    x0, x1 = x_range
    y0, y1 = _y_range(y)
    xticks = nice_ticks(x0, x1, 10)
    yticks = nice_ticks(y0, y1, 6)

    def px(v):
        return rect.left() + (v - x0) / (x1 - x0) * rect.width()

    def py(v):
        return rect.bottom() - (v - y0) / (y1 - y0) * rect.height()

    p.fillRect(rect, QColor(COLORS["graph_bg"]))
    grid = QPen(QColor(COLORS["graph_grid"]), 1)
    axis_text = QColor(COLORS["graph_axis"])
    p.setFont(mono_font(16))
    for t in xticks:
        p.setPen(grid)
        p.drawLine(QPointF(px(t), rect.top()), QPointF(px(t), rect.bottom()))
        if show_x_labels:
            p.setPen(axis_text)
            p.drawText(QRectF(px(t) - 60, rect.bottom() + 6, 120, 22), Qt.AlignHCenter | Qt.AlignTop, _fmt_tick(t, xticks))
    for t in yticks:
        p.setPen(grid)
        p.drawLine(QPointF(rect.left(), py(t)), QPointF(rect.right(), py(t)))
        p.setPen(axis_text)
        p.drawText(QRectF(rect.left() - 100, py(t) - 11, 92, 22), Qt.AlignRight | Qt.AlignVCenter, _fmt_tick(t, yticks))

    if len(x):
        xd, yd = minmax_decimate(x, y, int(rect.width()) * 2)
        pts = [QPointF(a, b) for a, b in zip(map(px, xd.tolist()), map(py, yd.tolist()))]
        p.save()
        p.setClipRect(rect)
        p.setPen(QPen(QColor(color), width))
        # drawPolyline は太さのある線の継ぎ目計算が遅いので、線分の集まりとして描く
        p.drawLines([QLineF(a, b) for a, b in zip(pts, pts[1:])])
        p.restore()
    else:
        p.setPen(axis_text)
        p.drawText(rect, Qt.AlignCenter, "データなし")

    p.setPen(QPen(QColor(COLORS["graph_grid"]), 1))
    p.drawRect(rect)
    # 縦軸ラベル
    p.save()
    p.setPen(QColor(label_color))
    p.setFont(ui_font(18, bold=True))
    p.translate(rect.left() - 92, rect.center().y())
    p.rotate(-90)
    p.drawText(QRectF(-rect.height() / 2, -24, rect.height(), 28), Qt.AlignCenter, label)
    p.restore()


def render_image(data: MeasurementData, title: str, note: str) -> QImage:
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
    x_end = float(x[-1]) if len(x) else 1.0
    x_range = (0.0, x_end if x_end > 0 else 1.0)
    left, right, top, bottom = 130, 40, 72, 120
    gap = 30
    plot_h = (HEIGHT - top - bottom - gap) / 2
    r_cur = QRectF(left, top, WIDTH - left - right, plot_h)
    r_vol = QRectF(left, top + plot_h + gap, WIDTH - left - right, plot_h)
    _draw_plot(p, r_cur, x, data.column("current_mA"), x_range, "電流 [mA]", COLORS["current_label"],
               COLORS["current_line"], CURRENT_LINE_WIDTH, False)
    _draw_plot(p, r_vol, x, data.column("bus_V"), x_range, "電圧 [V]", COLORS["voltage_label"],
               COLORS["voltage_line"], VOLTAGE_LINE_WIDTH, True)

    p.setPen(QColor(COLORS["text_sub"]))
    p.setFont(ui_font(18))
    p.drawText(QRectF(0, r_vol.bottom() + 28, WIDTH, 26), Qt.AlignCenter, "経過時間 [s]")
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
