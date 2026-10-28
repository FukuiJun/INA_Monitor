"""模擬シリアル：実機なしで Arduino + INA226 の出力をまねる（開発・AC-13 用）。

pyserial の Serial と同じく read(size) / in_waiting / write() / reset_input_buffer() / close() を持つ。
write() で受けた `INTERVAL <ms>` は付録 A のスケッチと同じく周期を変えて応答を返す。
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass

from .protocol import DEFAULT_INTERVAL_MS, MAX_INTERVAL_MS, MIN_INTERVAL_MS

SHUNT_OHM = 0.080
INA226_OFFSET_MA = -0.1  # 無負荷でも出るオフセット（実機と同程度）


@dataclass(frozen=True)
class MockProfile:
    load: bool = True  # False なら無負荷（-0.1 mA 前後）
    reset_delay_s: float = 1.2  # ポートを開いてからヘッダ行が出るまで
    reset_every_s: float | None = None  # Arduino のリセットを模擬する間隔
    disconnect_after_s: float | None = None  # USB 抜けを模擬する時刻
    invalid_every_s: float | None = 30.0  # 途中で切れた行を混ぜる間隔
    ignore_commands: bool = False  # True なら周期変更コマンドに応答しない古いスケッチ
    seed: int = 1


class MockArduinoState:
    """模擬 Arduino の設定。write() で変わり、行の生成側が読む。"""

    AVG_COUNTS = (1, 4, 16, 64, 128, 256, 512, 1024)

    def __init__(self) -> None:
        self.interval_ms = DEFAULT_INTERVAL_MS

    @classmethod
    def avg_for(cls, interval_ms: int) -> int:
        """スケッチと同じ規則：変換時間の合計が周期の 90% 以下になる最大の平均回数。"""
        return max(n for n in cls.AVG_COUNTS if n * 2.2 <= interval_ms * 0.9 or n == 1)

    def handle_command(self, line: str) -> str:
        """1 行のコマンドを処理して応答行を返す。"""
        if line.startswith("INTERVAL "):
            try:
                ms = int(line[9:])
            except ValueError:
                ms = -1
            if not MIN_INTERVAL_MS <= ms <= MAX_INTERVAL_MS:
                return "# error: interval out of range"
            self.interval_ms = ms
            return f"# interval_ms={ms} avg={self.avg_for(ms)}"
        return "# error: unknown command"


def true_current_mA(t: float, load: bool) -> float:
    if not load:
        return 0.0
    i = 25.0 + 10.0 * math.sin(2 * math.pi * t / 20.0)
    if t % 7.0 < 0.5:
        i += 120.0
    return i


def _data_line(time_ms: int, t: float, profile: MockProfile, rng: random.Random) -> str:
    i_true = true_current_mA(t, profile.load)
    i_meas = i_true + INA226_OFFSET_MA + rng.gauss(0.0, 0.02)
    shunt_uV = round(i_meas * SHUNT_OHM * 1000.0 / 2.5) * 2.5  # LSB 2.5 µV
    current_mA = shunt_uV / SHUNT_OHM / 1000.0
    if profile.load:
        bus_V = round((3.70 - 0.0004 * i_true + rng.gauss(0.0, 0.001)) / 1.25e-3) * 1.25e-3
    else:
        bus_V = 0.0
    return f"{time_ms},{bus_V:.4f},{shunt_uV:.1f},{current_mA:.3f}"


def mock_events(
    profile: MockProfile = MockProfile(), state: MockArduinoState | None = None
) -> Iterator[tuple[float, str | None]]:
    """(ポートを開いてからの秒, 行) を無限に返す。行が None なら USB 抜け。"""
    state = state or MockArduinoState()
    rng = random.Random(profile.seed)
    seg_start = 0.0  # Arduino 起動時刻（ポートを開いた時刻からの秒）
    next_invalid = profile.invalid_every_s
    while True:
        state.interval_ms = DEFAULT_INTERVAL_MS  # リセットで既定の周期に戻る
        t = seg_start + profile.reset_delay_s
        for line in ("# I2C scan...", "#  found 0x41", "time_ms,bus_V,shunt_uV,current_mA"):
            yield t, line
        time_ms = int(profile.reset_delay_s * 1000)
        while True:
            time_ms += state.interval_ms + (1 if rng.random() < 0.1 else 0)
            t = seg_start + time_ms / 1000.0
            if profile.disconnect_after_s is not None and t >= profile.disconnect_after_s:
                yield t, None
                return
            if profile.reset_every_s is not None and t - seg_start >= profile.reset_every_s:
                seg_start = t
                break
            if next_invalid is not None and t >= next_invalid:
                yield t, f"{time_ms},0.00"  # 途中で切れた行
                next_invalid += profile.invalid_every_s
            yield t, _data_line(time_ms, t, profile, rng)


def generate_lines(n_data: int, profile: MockProfile = MockProfile()) -> list[tuple[float, str]]:
    """データ行が n_data 行になるまでの (秒, 行) を一気に作る（性能テスト用）。"""
    out: list[tuple[float, str]] = []
    count = 0
    for t, line in mock_events(profile):
        if line is None:
            break
        out.append((t, line))
        if line[0].isdigit() and line.count(",") == 3:
            count += 1
            if count >= n_data:
                break
    return out


class MockSerial:
    def __init__(
        self,
        profile: MockProfile = MockProfile(),
        speed: float = 1.0,
        timeout: float = 0.1,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.profile = profile
        self.speed = speed
        self.timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._t0 = clock()
        self.state = MockArduinoState()
        self._events = mock_events(profile, self.state)
        self._cmd = bytearray()
        self._next = next(self._events)
        self._buf = bytearray()
        self._disconnected = False
        self.is_open = True

    def _now(self) -> float:
        return (self._clock() - self._t0) * self.speed

    def _pump(self) -> None:
        now = self._now()
        while not self._disconnected and self._next[0] <= now:
            line = self._next[1]
            if line is None:
                self._disconnected = True
                break
            self._buf.extend(line.encode("ascii") + b"\r\n")
            self._next = next(self._events)

    def _check(self) -> None:
        if not self.is_open:
            raise OSError("模擬シリアル：ポートは閉じています")
        if self._disconnected and not self._buf:
            raise OSError("模擬シリアル：USB が抜けました")

    @property
    def in_waiting(self) -> int:
        self._check()
        self._pump()
        return len(self._buf)

    def read(self, size: int = 1) -> bytes:
        deadline = self._clock() + self.timeout
        while True:
            self._check()
            self._pump()
            if self._buf:
                out = bytes(self._buf[:size])
                del self._buf[:size]
                return out
            if self._disconnected:
                continue  # 次の _check() で例外
            remaining = deadline - self._clock()
            if remaining <= 0:
                return b""
            wait = (self._next[0] - self._now()) / self.speed
            self._sleep(min(remaining, max(wait, 0.001)))

    def write(self, data: bytes) -> int:
        """PC からの送信。改行までを 1 コマンドとして処理し、応答をすぐ受信側に入れる。"""
        self._check()
        self._pump()
        self._cmd.extend(data)
        while b"\n" in self._cmd:
            line, _, rest = bytes(self._cmd).partition(b"\n")
            self._cmd = bytearray(rest)
            if self.profile.ignore_commands:
                continue
            reply = self.state.handle_command(line.decode("ascii", errors="replace").strip())
            self._buf.extend(reply.encode("ascii") + b"\r\n")
        return len(data)

    def reset_input_buffer(self) -> None:
        self._pump()
        self._buf.clear()

    def close(self) -> None:
        self.is_open = False
