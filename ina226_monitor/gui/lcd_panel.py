"""液晶パネル：状態・測定値・グラフをまとめた黒い表示部（SDL_DischargeLogger の液晶パネルにならう）。

  ┌ 縁（ベゼル）────────────────────────────────────┐
  │ ┌ 液晶 ─────────────────────────────────────┐ │
  │ │ ● 測定中　周期 50 ms（平均 16 回）   保存予定: …  │ │
  │ │ ─────────────────（オレンジの線）──────────── │ │
  │ │   42.381 mA      3.3016 V      139.920 mW     │ │
  │ │   9.9137 mAh                     00:14:02     │ │
  │ │ ─────────────────────────────────────────── │ │
  │ │ グラフ（PlotPanel）                             │ │
  │ └───────────────────────────────────────────┘ │
  └─────────────────────────────────────────────┘
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from ..theme import mono_font, ui_font
from .plot_panel import PlotPanel
from .widgets import FitLabel

BIG_PX = 40  # 1 段目（電流・電圧・電力）
SMALL_PX = 30  # 2 段目（積算・経過時間）

# 状態表示の種類 → 色の名前（app.qss の QLabel#lcdState[kind=…]）
STATE_IDLE = "idle"  # 未測定（灰色）
STATE_LIVE = "live"  # 測定中（黄）
STATE_DONE = "done"  # 停止後（白）


class ElideLabel(QLabel):
    """入りきらない文字を途中で「…」にするラベル（長いファイル名用）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        w = self.contentsRect().width()
        text = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, w) if w > 0 else self._full
        super().setText(text)


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("lcdRule")
    line.setFixedHeight(1)
    return line


def _restyle(widget) -> None:
    """動的プロパティを変えたあとに QSS を当て直す。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class LcdPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("lcdBezel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        lcd = QFrame()
        lcd.setObjectName("lcd")
        outer.addWidget(lcd)
        lay = QVBoxLayout(lcd)
        lay.setContentsMargins(16, 10, 16, 12)
        lay.setSpacing(8)

        # ---- 状態の行 ----
        row = QHBoxLayout()
        row.setSpacing(14)
        self.state_label = QLabel()
        self.state_label.setObjectName("lcdState")
        self.state_label.setFont(ui_font(14, bold=True))
        row.addWidget(self.state_label)
        self.interval_label = QLabel()
        self.interval_label.setObjectName("lcdInfo")
        self.interval_label.setFont(ui_font(13))
        row.addWidget(self.interval_label)
        self.file_label = ElideLabel()
        self.file_label.setObjectName("lcdInfo")
        self.file_label.setFont(ui_font(13))
        self.file_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.file_label, 1)
        lay.addLayout(row)
        lay.addWidget(_rule())

        # ---- 測定値（3 列 × 2 段、右寄せ） ----
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(0)
        self.current_label = self._value("lcdCurrent", BIG_PX)
        self.voltage_label = self._value("lcdVoltage", BIG_PX)
        self.power_label = self._value("lcdPower", BIG_PX)
        self.mah_label = self._value("lcdMah", SMALL_PX)
        self.elapsed_label = self._value("lcdElapsed", SMALL_PX)
        grid.addWidget(self.current_label, 0, 0)
        grid.addWidget(self.voltage_label, 0, 1)
        grid.addWidget(self.power_label, 0, 2)
        grid.addWidget(self.mah_label, 1, 0)
        grid.addWidget(self.elapsed_label, 1, 2)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)
        lay.addWidget(_rule())

        # ---- グラフ ----
        self.plot = PlotPanel()
        lay.addWidget(self.plot, 1)

        self.set_state("未測定", STATE_IDLE)

    @staticmethod
    def _value(name: str, px: int) -> FitLabel:
        label = FitLabel(mono_font(px, bold=True), min_px=14)
        label.setObjectName(name)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setProperty("dim", True)
        return label

    # ---- 外部から ----
    def set_state(self, text: str, kind: str) -> None:
        self.state_label.setText(text)
        if self.state_label.property("kind") != kind:
            self.state_label.setProperty("kind", kind)
            _restyle(self.state_label)

    def set_interval_text(self, text: str) -> None:
        self.interval_label.setText(text)

    def set_file_text(self, text: str) -> None:
        self.file_label.setText(text)

    def set_values(self, current: str, voltage: str, power: str, mah: str, elapsed: str, dim: bool) -> None:
        """測定値を表示する。dim=True ならまだ値が無い（--- を灰色で出す）。"""
        for label, text in ((self.current_label, current), (self.voltage_label, voltage),
                            (self.power_label, power), (self.mah_label, mah), (self.elapsed_label, elapsed)):
            label.setText(text)
            if bool(label.property("dim")) != dim:
                label.setProperty("dim", dim)
                _restyle(label)
