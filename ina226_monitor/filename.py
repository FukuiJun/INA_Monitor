"""出力ファイル名（基名）の生成（仕様 F-08）。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|\s]')

OUTPUT_EXTENSIONS = (".csv", ".png", ".txt")


def sanitize(text: str) -> str:
    """ファイル名に使えない文字と空白を _ にし、末尾の . を削除する。"""
    return _FORBIDDEN_RE.sub("_", text).rstrip(".")


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def build_base_name(
    started_at: datetime | None,
    target: str,
    board: str,
    operator: str,
    now: datetime | None = None,
) -> str:
    """`<YYYYMMDD_HHMMSS>_<測定対象>_<基板情報>_<測定者>`。空欄は飛ばす。"""
    dt = started_at or now or datetime.now()
    parts = [stamp(dt)]
    for value in (target, board, operator):
        value = value.strip()
        if value:
            parts.append(value)
    return sanitize("_".join(parts))


def unique_base_name(folder: Path, base: str, extensions=OUTPUT_EXTENSIONS) -> str:
    """同じ基名のファイルが 1 つでもあれば _2, _3 … を付ける。"""
    candidate = base
    k = 2
    while any((folder / f"{candidate}{ext}").exists() for ext in extensions):
        candidate = f"{base}_{k}"
        k += 1
    return candidate
