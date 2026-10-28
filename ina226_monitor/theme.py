"""画面と PNG の配色（仕様 付録 B）・フォント（仕様 7.3）。

色はここだけで定義する。余白・角丸などの見た目は style/app.qss にあり、
app.qss の「ドル記号＋{名前}」はこの COLORS で置き換える（名前_dis は無効時用の 40% 不透明の色）。
"""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QFont

from .resources import STYLE_QSS, resource_path

COLORS: dict[str, str] = {
    # 全体
    "window_bg": "#F3EBE1",  # 鳥の子色の派生
    "panel_bg": "#FCF8F3",
    "panel_border": "#E2D3C2",
    "divider": "#E2D3C2",
    "input_bg": "#FFFCF8",
    "input_border": "#B0927A",  # 利休白茶
    "readonly_bg": "#EFE4D7",
    "readonly_border": "#E2D3C2",
    "text": "#27221F",  # 墨色
    "text_sub": "#656255",  # 利休鼠
    # ヘッダー
    "header_bg": "#2B2A28",
    "header_text": "#FBF8F1",
    "accent_band": "#9D2933",  # 臙脂色
    "badge_bg": "#B4581E",  # 朽葉色を濃くした色
    "badge_text": "#FBF8F1",
    # 数値・ボタン
    "current_badge_bg": "#A3282C",
    "current_badge_text": "#FFFFFF",
    "start_bg": "#D57835",  # 朽葉色
    "start_text": "#FBF8F1",
    "stop_bg": "#9D2933",
    "stop_text": "#FCF8F3",
    "save_bg": "#27221F",
    "save_text": "#FCF8F3",
    "range_sel_bg": "#B4581E",
    "range_sel_text": "#FCF8F3",
    # ステータスバー
    "status_bg": "#E2BE9F",
    "status_text": "#27221F",
    # グラフ
    "current_label": "#9A4F1C",
    "voltage_label": "#4E7A3A",  # 濃い緑
    "graph_bg": "#2B2A28",  # 描画領域は暗色（墨色に近い濃色）
    "graph_border": "#1E1C1A",
    "graph_grid": "#45403A",
    "graph_axis": "#656255",  # 軸の数値（描画領域の外、明るいパネル上）
    "current_line": "#D57835",
    "voltage_line": "#8CC27A",  # 緑（萌黄系）
}

CURRENT_LINE_WIDTH = 2.0
VOLTAGE_LINE_WIDTH = 2.0
DISABLED_OPACITY = 0.4

UI_FONTS = ["Yu Gothic UI", "Meiryo UI", "Meiryo", "Noto Sans CJK JP", "sans-serif"]
MONO_FONTS = ["Consolas", "DejaVu Sans Mono", "Noto Sans Mono", "monospace"]


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def ui_font(px: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONTS)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def mono_font(px: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FONTS)
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def stylesheet() -> str:
    values = dict(COLORS)
    values.update({f"{k}_dis": rgba(v, DISABLED_OPACITY) for k, v in COLORS.items()})
    qss = resource_path(STYLE_QSS)
    values["style_dir"] = qss.parent.as_posix()  # 矢印などの画像の場所
    text = qss.read_text(encoding="utf-8")
    return Template(text).substitute(values)


def apply(app) -> None:
    """QApplication に配色・フォント・スタイルを当てる。"""
    app.setStyle("Fusion")  # QSS の角丸・色が OS の標準スタイルに邪魔されないように
    app.setFont(ui_font(13))
    app.setStyleSheet(stylesheet())
