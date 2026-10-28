"""測定中の一時ファイルと、異常終了からの復旧（仕様 F-10, F-11）。"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .formatting import CSV_COLUMNS_WITH_PC_TIME, csv_header, format_csv_rows

log = logging.getLogger(__name__)

TMP_CSV_SUFFIX = "_tmp.csv"
TMP_JSON_SUFFIX = "_tmp.json"
RECOVERED_SUFFIX = "_recovered.csv"

_STAMP_RE = re.compile(r"^(\d{8}_\d{6})(_\d+)?$")


def tmp_paths(recovery_dir: Path, name: str) -> tuple[Path, Path]:
    return recovery_dir / f"{name}{TMP_CSV_SUFFIX}", recovery_dir / f"{name}{TMP_JSON_SUFFIX}"


def unique_tmp_name(recovery_dir: Path, stamp: str) -> str:
    name, k = stamp, 2
    while any(p.exists() for p in tmp_paths(recovery_dir, name)):
        name, k = f"{stamp}_{k}", k + 1
    return name


class RecoveryWriter:
    """一時ファイルへの追記を別スレッドで行う。

    書き込みに失敗した分は保持しておき、次の submit() でまとめて書き直す。
    """

    def __init__(self, recovery_dir: Path, stamp: str, info: Mapping[str, str]) -> None:
        self.recovery_dir = Path(recovery_dir)
        self.name = unique_tmp_name(self.recovery_dir, stamp)
        self.csv_path, self.json_path = tmp_paths(self.recovery_dir, self.name)
        self._info = dict(info)
        self._queue: queue.Queue = queue.Queue()
        self._pending: list[dict[str, np.ndarray]] = []
        self._initialized = False
        self._lock = threading.Lock()
        self._last_write: float | None = None
        self._error = False
        self._rows_written = 0
        self._thread = threading.Thread(target=self._run, name="recovery-writer", daemon=True)
        self._thread.start()
        self._queue.put(("write", None))

    # ---- GUI スレッドから呼ぶ ----
    def submit(self, cols: dict[str, np.ndarray]) -> None:
        """前回以降のデータ行（列ごとの配列のコピー）を追記する。"""
        self._queue.put(("write", cols))

    def close(self, timeout: float = 10.0) -> bool:
        """未書き込み分を書いてスレッドを終える。成功したら True。"""
        self._queue.put(("close", None))
        self._thread.join(timeout)
        return not self._error and not self._pending

    def delete_files(self) -> None:
        if self._thread.is_alive():
            self.close()
        for p in (self.csv_path, self.json_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                log.warning("一時ファイルを削除できません: %s", p, exc_info=True)

    def status(self) -> tuple[float | None, bool, int]:
        """(最終書き込み時刻, 直近の書き込みが失敗したか, 書き込んだ行数)"""
        with self._lock:
            return self._last_write, self._error, self._rows_written

    # ---- 書き込みスレッド ----
    def _run(self) -> None:
        while True:
            kind, cols = self._queue.get()
            if cols is not None and len(cols["elapsed_s"]):
                self._pending.append(cols)
            self._try_write()
            if kind == "close":
                return

    def _try_write(self) -> None:
        try:
            if not self._initialized:
                self.recovery_dir.mkdir(parents=True, exist_ok=True)
                self.json_path.write_text(
                    json.dumps(self._info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                with open(self.csv_path, "w", encoding="utf-8-sig", newline="\r\n") as f:
                    f.write(csv_header(True) + "\n")
                self._initialized = True
            rows_added = 0
            if self._pending:
                text = "".join(
                    "".join(r + "\n" for r in format_csv_rows(chunk, True)) for chunk in self._pending
                )
                self._append(text)
                rows_added = sum(len(c["elapsed_s"]) for c in self._pending)
                self._pending.clear()
        except OSError:
            log.warning("一時ファイルに書き込めません: %s", self.csv_path, exc_info=True)
            with self._lock:
                self._error = True
            return
        with self._lock:
            self._error = False
            self._last_write = time.time()
            self._rows_written += rows_added

    def _append(self, text: str) -> None:
        # 途中まで書けて失敗した場合は元の長さに戻す（再試行で行が重複しないように）
        size = self.csv_path.stat().st_size
        try:
            with open(self.csv_path, "a", encoding="utf-8", newline="\r\n") as f:
                f.write(text)
                f.flush()
        except OSError:
            try:
                with open(self.csv_path, "r+b") as f:
                    f.truncate(size)
            except OSError:
                pass
            raise


# ---- 復旧 ----
@dataclass
class RecoveryItem:
    name: str  # 一時ファイル名から _tmp.csv を除いたもの
    csv_path: Path
    json_path: Path
    info: dict
    row_count: int

    @property
    def stamp(self) -> str:
        m = _STAMP_RE.match(self.name)
        return m.group(1) if m else self.name


_N_COLS = len(CSV_COLUMNS_WITH_PC_TIME)


def _is_valid_row(line: str) -> bool:
    cols = line.split(",")
    if len(cols) != _N_COLS:
        return False
    try:
        for i, c in enumerate(cols):
            if i != 1:
                float(c)
    except ValueError:
        return False
    return len(cols[1]) == len("2026-10-28 14:30:17.350")


def read_valid_lines(csv_path: Path) -> tuple[str, list[str]]:
    """(列名行, 有効なデータ行) を返す。途中で切れた最終行などは除く。"""
    raw = csv_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    parts = text.split("\n")
    # 改行で終わっていない最後の断片は書き込み途中で切れた行
    complete = parts[:-1]
    lines = [p.rstrip("\r") for p in complete]
    if not lines:
        return csv_header(True), []
    header, rows = lines[0], lines[1:]
    return header, [r for r in rows if _is_valid_row(r)]


def find_recoverable(recovery_dir: Path) -> list[RecoveryItem]:
    items: list[RecoveryItem] = []
    if not recovery_dir.is_dir():
        return items
    for csv_path in sorted(recovery_dir.glob(f"*{TMP_CSV_SUFFIX}")):
        name = csv_path.name[: -len(TMP_CSV_SUFFIX)]
        json_path = recovery_dir / f"{name}{TMP_JSON_SUFFIX}"
        try:
            info = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(info, dict):
                info = {}
        except (OSError, ValueError):
            info = {}
        try:
            _, rows = read_valid_lines(csv_path)
        except OSError:
            log.warning("一時ファイルを読めません: %s", csv_path, exc_info=True)
            continue
        item = RecoveryItem(name, csv_path, json_path, info, len(rows))
        if item.row_count == 0:
            # 復旧するデータが無い（最初の追記前に終了した）ものは黙って消す
            delete_item(item)
            continue
        items.append(item)
    # 一時 CSV が無い JSON だけの残骸も消す
    for json_path in recovery_dir.glob(f"*{TMP_JSON_SUFFIX}"):
        if not (recovery_dir / (json_path.name[: -len(TMP_JSON_SUFFIX)] + TMP_CSV_SUFFIX)).exists():
            try:
                json_path.unlink()
            except OSError:
                pass
    return items


def recover_to(item: RecoveryItem, dest_dir: Path) -> Path:
    """`<測定日時>_recovered.csv` としてコピーする（PNG・TXT は作らない）。"""
    header, rows = read_valid_lines(item.csv_path)
    dest_dir = Path(dest_dir)
    base = f"{item.stamp}_recovered"
    dest = dest_dir / f"{base}.csv"
    k = 2
    while dest.exists():
        dest = dest_dir / f"{base}_{k}.csv"
        k += 1
    with open(dest, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(header + "\n")
        f.write("".join(r + "\n" for r in rows))
    return dest


def delete_item(item: RecoveryItem) -> None:
    for p in (item.csv_path, item.json_path):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            log.warning("一時ファイルを削除できません: %s", p, exc_info=True)
