"""測定データの保持と計算（ゼロ点補正・電力・積算 mAh・経過時間）。仕様 F-05, F-12。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MS_PER_HOUR = 3_600_000.0


def apply_offset(current_raw_mA: float, offset_mA: float | None) -> float:
    """ゼロ点補正：生データから補正値を差し引く。補正なしならそのまま。"""
    return current_raw_mA if offset_mA is None else current_raw_mA - offset_mA


def power_mW(bus_V: float, current_mA: float) -> float:
    return bus_V * current_mA


def trapezoid_mAh(i_prev_mA: float, i_mA: float, dt_ms: float) -> float:
    return (i_prev_mA + i_mA) / 2.0 * dt_ms / MS_PER_HOUR


@dataclass(frozen=True)
class AddResult:
    reset_detected: bool


class MeasurementData:
    """1 回の測定のデータ。全サンプルを numpy 配列で持つ（8 時間 ≒ 576,000 行を想定）。

    current_mA にはゼロ点補正後の値を入れる。
    """

    COLUMNS = ("elapsed_s", "pc_time", "bus_V", "current_mA", "power_mW", "mAh")
    _INITIAL_CAPACITY = 1 << 16

    def __init__(self, offset_mA: float | None = None) -> None:
        self.offset_mA = offset_mA
        self.invalid_count = 0
        self.reset_count = 0
        self._n = 0
        self._cap = self._INITIAL_CAPACITY
        self._cols = {name: np.empty(self._cap, dtype=np.float64) for name in self.COLUMNS}
        # 積算・経過時間の状態
        self._mAh = 0.0
        self._last_time_ms: int | None = None
        self._last_current = 0.0
        self._last_pc_time = 0.0
        self._last_elapsed_ms = 0.0
        self._segment_start_ms = 0
        self._segment_base_ms = 0.0

    # ---- 追加 ----
    def add(self, time_ms: int, bus_V: float, current_raw_mA: float, pc_time: float) -> AddResult:
        current = apply_offset(current_raw_mA, self.offset_mA)
        reset = False
        if self._last_time_ms is None:
            self._segment_start_ms = time_ms
            self._segment_base_ms = 0.0
        elif time_ms < self._last_time_ms:
            # Arduino のリセット：ここを区切りとし、区切りをまたぐ区間は積算しない。
            # 経過時間は PC 時刻の差分でつなぐ（Arduino 側の時刻は 0 から再開するため）。
            reset = True
            self.reset_count += 1
            gap_ms = max(0.0, (pc_time - self._last_pc_time) * 1000.0)
            self._segment_base_ms = self._last_elapsed_ms + gap_ms
            self._segment_start_ms = time_ms
        else:
            self._mAh += trapezoid_mAh(self._last_current, current, time_ms - self._last_time_ms)

        elapsed_ms = self._segment_base_ms + (time_ms - self._segment_start_ms)
        self._last_time_ms = time_ms
        self._last_current = current
        self._last_pc_time = pc_time
        self._last_elapsed_ms = elapsed_ms

        if self._n == self._cap:
            self._grow()
        i = self._n
        c = self._cols
        c["elapsed_s"][i] = elapsed_ms / 1000.0
        c["pc_time"][i] = pc_time
        c["bus_V"][i] = bus_V
        c["current_mA"][i] = current
        c["power_mW"][i] = power_mW(bus_V, current)
        c["mAh"][i] = self._mAh
        self._n += 1
        return AddResult(reset)

    def _grow(self) -> None:
        self._cap *= 2
        for name, arr in self._cols.items():
            new = np.empty(self._cap, dtype=np.float64)
            new[: self._n] = arr[: self._n]
            self._cols[name] = new

    # ---- 参照 ----
    def __len__(self) -> int:
        return self._n

    @property
    def count(self) -> int:
        return self._n

    @property
    def mAh(self) -> float:
        return self._mAh

    def column(self, name: str) -> np.ndarray:
        """先頭から現在までのビュー（コピーしない。追加で配列が作り直されると古いビューになる）。"""
        return self._cols[name][: self._n]

    def slice_copy(self, start: int, end: int | None = None) -> dict[str, np.ndarray]:
        end = self._n if end is None else min(end, self._n)
        return {name: arr[start:end].copy() for name, arr in self._cols.items()}

    def last(self) -> dict[str, float] | None:
        if self._n == 0:
            return None
        i = self._n - 1
        return {name: float(arr[i]) for name, arr in self._cols.items()}

    @property
    def last_elapsed_s(self) -> float:
        return self._last_elapsed_ms / 1000.0 if self._n else 0.0
