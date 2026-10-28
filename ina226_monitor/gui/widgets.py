"""画面部品：数値ラベル・区切り線・保存オプションのダイアログ。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ..theme import mono_font, ui_font


class FitLabel(QLabel):
    """等幅・右寄せの数値ラベル。桁が増えて入りきらないときは文字を小さくする。"""

    def __init__(self, base_font: QFont, min_px: int = 10, parent=None) -> None:
        super().__init__(parent)
        self._base = QFont(base_font)
        self._min = min_px
        self.setFont(self._base)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt の名前に合わせる)
        super().setText(text)
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        avail = self.contentsRect().width()
        f = QFont(self._base)
        px = f.pixelSize()
        while px > self._min and QFontMetrics(f).horizontalAdvance(self.text()) > avail > 0:
            px -= 1
            f.setPixelSize(px)
        if f != self.font():
            self.setFont(f)


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def sub_label(text: str, px: int = 13) -> QLabel:
    """補助の文字色（項目名・単位）のラベル。"""
    label = QLabel(text)
    label.setProperty("role", "sub")
    label.setFont(ui_font(px))
    return label


class SaveOptionsDialog(QDialog):
    """保存ボタンを押したときに保存先フォルダと PC 日時の出力を選ぶ（仕様 Q-16）。"""

    def __init__(self, parent, folder: str, include_pc_time: bool, base: str, with_txt: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("保存")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)

        lay.addWidget(sub_label("保存先フォルダ"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit(folder)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setFont(ui_font(13))
        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        self.pc_time_check = QCheckBox("PC の日時を出力する（CSV の 2 列目に pc_time）")
        self.pc_time_check.setChecked(include_pc_time)
        lay.addWidget(self.pc_time_check)

        exts = ".csv / .png / .txt" if with_txt else ".csv / .png"
        lay.addWidget(sub_label("保存するファイル"))
        files = QLabel(f"{base}（{exts}）")
        files.setFont(mono_font(12))
        files.setWordWrap(True)
        files.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(files)

        buttons = QDialogButtonBox()
        ok = buttons.addButton("保存", QDialogButtonBox.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.RejectRole)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダ", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    @property
    def folder(self) -> str:
        return self.folder_edit.text()

    @property
    def include_pc_time(self) -> bool:
        return self.pc_time_check.isChecked()
