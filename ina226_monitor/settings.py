"""前回値の保存（%APPDATA%\\INA226_Monitor\\settings.json）。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    last_port: str = ""
    board: str = ""
    target: str = ""
    operator: str = ""
    save_dir: str = ""
    include_pc_time: bool = False
    interval_ms: int = 50
    plot_mode: str = "split"  # グラフの表示モード（split：2 段／combined：まとめて表示）

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("設定ファイルを読めません: %s", path, exc_info=True)
            return cls()
        s = cls()
        for f in fields(cls):
            if f.name in raw and isinstance(raw[f.name], type(getattr(s, f.name))):
                setattr(s, f.name, raw[f.name])
        return s

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.warning("設定ファイルを書けません: %s", path, exc_info=True)
