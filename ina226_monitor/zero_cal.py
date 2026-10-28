"""ゼロ点補正（仕様 F-12）。"""

from __future__ import annotations

import threading
import time
from enum import Enum

from .protocol import LineKind, LineSplitter, parse_line

SAMPLE_COUNT = 20
TIMEOUT_S = 10.0
MAX_ABS_OFFSET_MA = 5.0
# ポートを開いた直後に届く行はリセット前の残りの可能性があるので使わない
STARTUP_DISCARD_S = 0.3


class ZeroCalFailure(Enum):
    NO_DATA = "データを受信できませんでした"
    LOAD_DETECTED = "負荷が接続されている可能性があります"
    CANCELLED = "キャンセルしました"


class ZeroCalError(Exception):
    def __init__(self, failure: ZeroCalFailure, value: float | None = None) -> None:
        super().__init__(failure.value)
        self.failure = failure
        self.value = value


def compute_offset(values: list[float]) -> float:
    """生データ current_mA の平均を補正値にする（0.001 mA 単位に丸める）。"""
    if len(values) < SAMPLE_COUNT:
        raise ZeroCalError(ZeroCalFailure.NO_DATA)
    mean = sum(values) / len(values)
    if abs(mean) > MAX_ABS_OFFSET_MA:
        raise ZeroCalError(ZeroCalFailure.LOAD_DETECTED, mean)
    return round(mean, 3) + 0.0  # -0.0 を 0.0 にする


def collect_samples(
    port,
    n: int = SAMPLE_COUNT,
    timeout_s: float = TIMEOUT_S,
    stop_event: threading.Event | None = None,
    clock=time.monotonic,
) -> list[float]:
    """開いたポートからデータ行を n 行集め、生の current_mA を返す。

    port は read(size) と in_waiting を持つもの（pyserial / 模擬シリアル）。
    """
    splitter = LineSplitter()
    values: list[float] = []
    start = clock()
    while len(values) < n:
        now = clock()
        if now - start > timeout_s:
            raise ZeroCalError(ZeroCalFailure.NO_DATA)
        if stop_event is not None and stop_event.is_set():
            raise ZeroCalError(ZeroCalFailure.CANCELLED)
        try:
            chunk = port.read(max(1, port.in_waiting))
        except OSError as e:
            raise ZeroCalError(ZeroCalFailure.NO_DATA) from e
        for line in splitter.feed(chunk):
            p = parse_line(line)
            if p.kind is LineKind.DATA and clock() - start >= STARTUP_DISCARD_S:
                values.append(p.row.current_mA)
                if len(values) >= n:
                    break
    return values


def format_offset(offset_mA: float | None) -> str:
    return "なし" if offset_mA is None else f"{offset_mA:.3f} mA"
