"""測定結果の保存（CSV・PNG・TXT）。仕様 5.2, F-08。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, __version__
from .filename import OUTPUT_EXTENSIONS, unique_base_name
from .formatting import csv_header, format_csv_rows, format_hms
from .measurement import MeasurementData
from .zero_cal import format_offset

log = logging.getLogger(__name__)

SHUNT_OHM_TEXT = "0.080 Ω（Arduino スケッチ設定値）"
CSV_CHUNK_ROWS = 20_000


@dataclass
class MeasurementInfo:
    board: str = ""
    target: str = ""
    operator: str = ""
    started_at: str = ""  # YYYY-MM-DD HH:MM:SS
    ended_at: str = ""
    memo: str = ""
    interval_ms: int | None = None  # Arduino が応答した周期（応答なしは None）
    avg: int | None = None  # INA226 の平均回数（応答の値）

    @property
    def has_memo(self) -> bool:
        return bool(self.memo.strip())


@dataclass
class SaveResult:
    folder: Path
    base: str
    paths: list[Path] = field(default_factory=list)


class SaveError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path.name} に書き込めません。Excel などで開いていないか確認してください")
        self.path = path
        self.cause = cause


def build_title(info: MeasurementInfo) -> str:
    parts = [info.target, info.board, info.operator, info.started_at]
    return " / ".join(p.strip() for p in parts if p.strip())


def format_interval(info: MeasurementInfo) -> str:
    if info.interval_ms is None:
        return "不明（Arduino から応答なし）"
    avg = f"（INA226 平均 {info.avg} 回）" if info.avg else ""
    return f"{info.interval_ms} ms{avg}"


def build_note(data: MeasurementData, info: MeasurementInfo | None = None) -> str:
    note = (
        f"積算 {data.mAh:.4f} mAh ／ 測定時間 {format_hms(data.last_elapsed_s)} ／ "
        f"サンプル数 {data.count} ／ ゼロ点補正 {format_offset(data.offset_mA)}"
    )
    if info is not None:
        interval = f"{info.interval_ms} ms" if info.interval_ms is not None else "不明"
        note += f" ／ 周期 {interval}"
    return note


def build_txt(data: MeasurementData, info: MeasurementInfo) -> str:
    memo = info.memo.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        f"基板情報: {info.board}",
        f"測定対象: {info.target}",
        f"測定者: {info.operator}",
        f"測定日時: {info.started_at}",
        f"終了日時: {info.ended_at}",
        f"測定時間: {format_hms(data.last_elapsed_s)}",
        f"サンプル数: {data.count}",
        f"サンプリング周期: {format_interval(info)}",
        f"不正行数: {data.invalid_count}",
        f"積算電流量: {data.mAh:.4f} mAh",
        f"ゼロ点補正値: {format_offset(data.offset_mA)}",
        f"ソフトウェア: {APP_NAME} v{__version__}",
        f"シャント抵抗: {SHUNT_OHM_TEXT}",
        "",
        "[メモ]",
        memo,
    ]
    return "\n".join(lines) + ("" if memo.endswith("\n") else "\n")


def write_csv(
    path: Path,
    data: MeasurementData,
    include_pc_time: bool,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """UTF-8（BOM 付き）・CRLF で書く。"""
    n = data.count
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(csv_header(include_pc_time) + "\n")
        for start in range(0, n, CSV_CHUNK_ROWS):
            rows = format_csv_rows(data.slice_copy(start, start + CSV_CHUNK_ROWS), include_pc_time)
            f.write("\n".join(rows))
            f.write("\n")
            if progress:
                progress(min(start + CSV_CHUNK_ROWS, n), n)


def write_txt(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)


def save_measurement(
    folder: Path,
    base: str,
    data: MeasurementData,
    info: MeasurementInfo,
    include_pc_time: bool,
    png_bytes: bytes,
    progress: Callable[[int, int], None] | None = None,
) -> SaveResult:
    """CSV・PNG（メモがあれば TXT）を保存する。

    同じ基名のファイルがあれば _2, _3 … を付ける。途中で失敗した場合は、今回書いたファイルを
    消してから SaveError を出す（同じ名前で再保存できるようにするため）。
    """
    folder = Path(folder)
    base = unique_base_name(folder, base, OUTPUT_EXTENSIONS)
    result = SaveResult(folder, base)
    csv_path, png_path, txt_path = (folder / f"{base}{ext}" for ext in OUTPUT_EXTENSIONS)

    def attempt(path: Path, fn: Callable[[], None]) -> None:
        try:
            fn()
        except OSError as e:
            log.warning("保存に失敗: %s", path, exc_info=True)
            for p in [*result.paths, path]:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SaveError(path, e) from e
        result.paths.append(path)

    attempt(csv_path, lambda: write_csv(csv_path, data, include_pc_time, progress))
    attempt(png_path, lambda: png_path.write_bytes(png_bytes))
    if info.has_memo:
        attempt(txt_path, lambda: write_txt(txt_path, build_txt(data, info)))
    return result
