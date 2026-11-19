"""グラフの軸の範囲と目盛り（画面用）。SDL_DischargeLogger の液晶スタイルのグラフにならう。

- 縦軸：下端は 0（マイナスがあればその下の区切り）、上端は区切りのよい値に切り上げ、目盛りはおよそ 5 等分
- 横軸：区切りのよい間隔の目盛り。1 分未満の間隔なら m:ss、それ以上なら h:mm で書く
GUI に依存しないのでテストできる。
"""

from __future__ import annotations

import math

# 縦軸の目盛り間隔の候補（× 10^n）
_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)
Y_DIVISIONS = 5
Y_HEADROOM = 0.05  # 最大値と上端がこれ（幅に対する割合）より近いときは 1 目盛り足す

# 横軸の目盛り間隔の候補［秒］
TIME_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200)
TIME_MAX_TICKS = 8
LIVE_LOOKAHEAD = 1.2  # 測定中の「全体」表示：経過時間の 1.2 倍まで先に取る（軸が頻繁に変わらないように）
FIT_MARGIN = 1.05  # 停止後の「全体」表示：測定時間に 5% だけ余白を足す
MIN_LIVE_SPAN_S = 60.0
MIN_FIT_SPAN_S = 10.0


def nice_step(raw: float) -> float:
    """raw 以上で最小の「1, 2, 2.5, 5 × 10^n」。"""
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in _NICE_STEPS if m * mag >= raw * (1 - 1e-9))


def nice_axis(ymin: float, ymax: float, divisions: int = Y_DIVISIONS) -> tuple[float, float, list[float]]:
    """縦軸の (下端, 上端, 目盛り)。

    下端は 0（ymin がマイナスなら区切りのよい値まで下げる）。上端は区切りのよい値に切り上げ、
    波形が枠に張り付かないよう、最大値との差が幅の 5% 未満なら 1 目盛り足す。
    divisions はおおよその目盛りの区切り数（高さの低いグラフでは減らす）。
    """
    if not (math.isfinite(ymin) and math.isfinite(ymax)):
        return 0.0, 1.0, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    lo, hi = min(0.0, ymin), max(0.0, ymax)
    if hi - lo <= 0:
        return 0.0, 1.0, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    step = nice_step((hi - lo) / divisions)
    bottom = math.floor(lo / step + 1e-9) * step
    top = math.ceil(hi / step - 1e-9) * step
    if ymax > 0 and top - ymax < (top - bottom) * Y_HEADROOM:
        top += step
    if ymin < 0 and ymin - bottom < (top - bottom) * Y_HEADROOM:
        bottom -= step
    n = int(round((top - bottom) / step))
    ticks = [_clean(bottom + step * k) for k in range(n + 1)]
    return _clean(bottom), _clean(top), ticks


def _clean(v: float) -> float:
    v = round(v, 9)
    return 0.0 if v == 0 else v


def tick_decimals(step: float) -> int:
    """目盛り間隔に合う小数の桁数（1 → 0 桁、0.5 → 1 桁、0.25 → 2 桁）。"""
    d = max(0, -math.floor(math.log10(step) + 1e-9))
    if abs(round(step, d) - step) > step * 1e-6:
        d += 1
    return d


def format_y_ticks(ticks: list[float]) -> list[str]:
    if len(ticks) < 2:
        return [f"{t:g}" for t in ticks]
    d = tick_decimals(abs(ticks[1] - ticks[0]))
    return [f"{t:.{d}f}" for t in ticks]


# ---- 横軸（時間） ----
def time_step(span_s: float) -> float:
    """幅 span_s［秒］で目盛りが TIME_MAX_TICKS 区切り以下になる最小の間隔［秒］。"""
    for st in TIME_STEPS_S:
        if span_s / st <= TIME_MAX_TICKS + 1e-9:
            return float(st)
    return float(TIME_STEPS_S[-1])


def full_time_range(elapsed_s: float, live: bool) -> tuple[float, float, float]:
    """「全体」表示の (左端, 右端, 目盛り間隔)［秒］。

    測定中は経過時間の 1.2 倍（最低 1 分）を区切りのよい値に切り上げる（先取りして軸が飛ばないように）。
    停止後は測定時間に 5% の余白を足して合わせる。
    """
    raw = max(elapsed_s * LIVE_LOOKAHEAD, MIN_LIVE_SPAN_S) if live else max(elapsed_s * FIT_MARGIN, MIN_FIT_SPAN_S)
    step = time_step(raw)
    return 0.0, math.ceil(raw / step - 1e-9) * step, step


def time_ticks(x0: float, x1: float, step: float | None = None) -> tuple[list[float], float]:
    """[x0, x1] に入る目盛り［秒］と間隔。"""
    step = step or time_step(max(x1 - x0, 1e-6))
    first = math.ceil(x0 / step - 1e-9)
    last = math.floor(x1 / step + 1e-9)
    return [k * step for k in range(first, last + 1)], step


def uses_seconds(step_s: float) -> bool:
    """間隔が 1 分未満なら m:ss、そうでなければ h:mm で書く。"""
    return step_s < 60 - 1e-9


def format_time_tick(t_s: float, step_s: float) -> str:
    total = int(round(t_s))
    if uses_seconds(step_s):
        return f"{total // 60}:{total % 60:02d}"
    minutes = int(round(t_s / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"


def time_unit(step_s: float) -> str:
    """最後の目盛りに付ける単位（m:ss なら「分」、h:mm なら「h」）。"""
    return "分" if uses_seconds(step_s) else "h"
