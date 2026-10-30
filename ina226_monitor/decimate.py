"""グラフ表示用の間引き（画面・PNG 共通）。保存データは間引かない。"""

from __future__ import annotations

import numpy as np


def visible_slice(x: np.ndarray, x0: float, x1: float) -> slice:
    """x（昇順）のうち [x0, x1] にかかる範囲。両端の外側 1 点ずつも含める（線を端まで描くため）。"""
    i0 = max(int(np.searchsorted(x, x0, side="left")) - 1, 0)
    i1 = min(int(np.searchsorted(x, x1, side="right")) + 1, len(x))
    return slice(i0, i1)


def minmax_decimate(x: np.ndarray, y: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    """点数を max_points 程度に減らす。区間ごとに最小・最大を残すので、細いピークも消えない。"""
    n = len(x)
    if n <= max_points or max_points < 4:
        return x, y
    bins = max_points // 2
    edges = np.linspace(0, n, bins + 1).astype(np.int64)
    starts = edges[:-1]
    ends = edges[1:] - 1
    ymin = np.minimum.reduceat(y, starts)
    ymax = np.maximum.reduceat(y, starts)
    xo = np.empty(bins * 2)
    yo = np.empty(bins * 2)
    xo[0::2] = x[starts]
    xo[1::2] = x[ends]
    yo[0::2] = ymin
    yo[1::2] = ymax
    return xo, yo


TOP_MARGIN_RATIO = 0.15  # 縦軸の上端の余白（表示範囲の幅に対する割合）
BOTTOM_MARGIN_RATIO = 0.05  # マイナスの値があるときの下端の余白


def zero_based_range(
    y: np.ndarray, top_ratio: float = TOP_MARGIN_RATIO, bottom_ratio: float = BOTTOM_MARGIN_RATIO
) -> tuple[float, float]:
    """縦軸の表示範囲。下端は 0（値がマイナスになるときだけ、その値より少し下まで広げる）。

    上端はデータの最大値の上に余白を付けて、波形が枠に張り付かないようにする。
    データが無い・すべて 0 のときは 0〜1。
    """
    if len(y) == 0:
        return 0.0, 1.0
    ymin, ymax = float(np.min(y)), float(np.max(y))
    lo, hi = min(0.0, ymin), max(0.0, ymax)
    span = hi - lo
    if span <= 0:
        return 0.0, 1.0
    if ymax > 0:
        hi += span * top_ratio
    if ymin < 0:
        lo -= span * bottom_ratio
    return lo, hi
