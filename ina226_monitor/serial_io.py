"""COM ポートの一覧・接続と、受信スレッド（仕様 F-01, F-02）。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from .mock_serial import MockProfile, MockSerial
from .protocol import LineKind, LineSplitter, parse_line
from .zero_cal import STARTUP_DISCARD_S

log = logging.getLogger(__name__)

BAUDRATE = 115200

# 模擬シリアル（--mock 起動時だけ一覧に出す）
MOCK_PORTS: dict[str, tuple[str, MockProfile]] = {
    "MOCK1": ("模擬データ（負荷あり）", MockProfile(load=True)),
    "MOCK2": ("模擬データ（無負荷）", MockProfile(load=False)),
    "MOCK3": ("模擬データ（20 秒後に USB 抜け）", MockProfile(disconnect_after_s=20.0)),
    "MOCK4": ("模擬データ（30 秒ごとにリセット）", MockProfile(reset_every_s=30.0)),
}


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str

    @property
    def label(self) -> str:
        return f"{self.device} - {self.description}" if self.description else self.device


class PortOpenError(Exception):
    def __init__(self, device: str) -> None:
        super().__init__(f"{device} を開けません。シリアルモニタなど他のソフトを閉じてください")
        self.device = device


def _clean_description(device: str, desc: str | None) -> str:
    desc = (desc or "").strip()
    if desc.lower() == "n/a" or desc == device:
        return ""
    # Windows では「Arduino Uno (COM7)」のようにポート名が付いている
    suffix = f"({device})"
    if desc.endswith(suffix):
        desc = desc[: -len(suffix)].strip()
    return desc


def list_ports(include_mock: bool = False) -> list[PortInfo]:
    ports: list[PortInfo] = []
    try:
        from serial.tools import list_ports as lp

        for p in sorted(lp.comports(), key=lambda p: p.device):
            ports.append(PortInfo(p.device, _clean_description(p.device, p.description)))
    except Exception:
        log.warning("COM ポート一覧を取得できません", exc_info=True)
    if include_mock:
        ports.extend(PortInfo(dev, desc) for dev, (desc, _) in MOCK_PORTS.items())
    return ports


def open_port(device: str, mock_speed: float = 1.0):
    """ポートを開く。開けなければ PortOpenError。"""
    if device in MOCK_PORTS:
        return MockSerial(MOCK_PORTS[device][1], speed=mock_speed)
    try:
        import serial

        ser = serial.Serial(
            device,
            BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        ser.reset_input_buffer()
        return ser
    except Exception as e:
        log.warning("ポートを開けません: %s", device, exc_info=True)
        raise PortOpenError(device) from e


class SerialReader(threading.Thread):
    """受信・行判定を行い、結果をキューに入れる。

    キューに入るもの：
      ("line", ParsedLine, pc_time)  … 判定済みの 1 行（pc_time は受信時の PC 時刻）
      ("disconnected", message)      … 読み込みで例外（USB 抜けなど）。スレッドは終わる
    """

    def __init__(self, port, out: queue.Queue, clock=time.monotonic) -> None:
        super().__init__(name="serial-reader", daemon=True)
        self.port = port
        self.out = out
        self._clock = clock
        self._stop_event = threading.Event()

    def run(self) -> None:
        splitter = LineSplitter()
        t_open = self._clock()
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self.port.read(max(1, self.port.in_waiting))
                except Exception as e:  # SerialException は OSError の派生だが念のため広く拾う
                    if not self._stop_event.is_set():
                        log.warning("受信中に接続が切れました: %s", e)
                        self.out.put(("disconnected", str(e)))
                    return
                if not chunk:
                    continue
                pc_time = time.time()
                for line in splitter.feed(chunk):
                    parsed = parse_line(line)
                    # 開いた直後の行は Arduino のリセット前の残りかもしれないので捨てる
                    if parsed.kind is LineKind.DATA and self._clock() - t_open < STARTUP_DISCARD_S:
                        continue
                    self.out.put(("line", parsed, pc_time))
        finally:
            try:
                self.port.close()
            except Exception:
                pass

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self.join(timeout)
