"""表示・ファイル出力用の書式。"""

from __future__ import annotations

import time
from collections.abc import Mapping

import numpy as np

CSV_COLUMNS_WITH_PC_TIME = ("elapsed_s", "pc_time", "bus_V", "current_mA", "power_mW", "mAh")
CSV_COLUMNS = tuple(c for c in CSV_COLUMNS_WITH_PC_TIME if c != "pc_time")


def format_hms(seconds: float) -> str:
    """経過秒を hh:mm:ss にする（端数は切り捨て）。"""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_datetime(t: float) -> str:
    """エポック秒を `YYYY-MM-DD HH:MM:SS`（ローカル時刻）にする。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


class PcTimeFormatter:
    """エポック秒を `YYYY-MM-DD HH:MM:SS.mmm` にする。秒部分をキャッシュして高速化する。"""

    def __init__(self) -> None:
        self._sec = None
        self._text = ""

    def __call__(self, t: float) -> str:
        ms_total = int(t * 1000.0)
        sec, ms = divmod(ms_total, 1000)
        if sec != self._sec:
            self._sec = sec
            self._text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sec))
        return f"{self._text}.{ms:03d}"


def csv_header(include_pc_time: bool) -> str:
    return ",".join(CSV_COLUMNS_WITH_PC_TIME if include_pc_time else CSV_COLUMNS)


def format_csv_rows(cols: Mapping[str, np.ndarray], include_pc_time: bool) -> list[str]:
    """列ごとの配列から CSV のデータ行（改行なし）を作る。"""
    elapsed = cols["elapsed_s"].tolist()
    bus = cols["bus_V"].tolist()
    cur = cols["current_mA"].tolist()
    pwr = cols["power_mW"].tolist()
    mah = cols["mAh"].tolist()
    if include_pc_time:
        fmt_pc = PcTimeFormatter()
        pc = cols["pc_time"].tolist()
        return [
            f"{e:.3f},{fmt_pc(p)},{b:.4f},{c:.3f},{w:.3f},{m:.4f}"
            for e, p, b, c, w, m in zip(elapsed, pc, bus, cur, pwr, mah)
        ]
    return [
        f"{e:.3f},{b:.4f},{c:.3f},{w:.3f},{m:.4f}"
        for e, b, c, w, m in zip(elapsed, bus, cur, pwr, mah)
    ]
