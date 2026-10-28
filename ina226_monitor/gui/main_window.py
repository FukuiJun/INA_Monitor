"""メインウィンドウ（仕様 7 章）。測定の流れ（開始・停止・保存・ゼロ点補正）をまとめる。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..filename import build_base_name, sanitize, stamp
from ..formatting import format_hms
from ..measurement import MeasurementData
from ..mock_serial import MockProfile, generate_lines
from ..paths import AppPaths, default_save_dir
from ..png_export import image_to_png_bytes, render_image
from ..protocol import LineKind, parse_line
from ..recovery import RecoveryWriter, delete_item, find_recoverable, recover_to
from ..serial_io import PortOpenError, SerialReader, list_ports, open_port
from ..settings import Settings
from ..storage import MeasurementInfo, SaveError, build_note, build_title, save_measurement
from ..zero_cal import ZeroCalError, ZeroCalFailure, collect_samples, compute_offset, format_offset
from .plot_panel import DEFAULT_RANGE, PlotPanel

log = logging.getLogger(__name__)

UPDATE_INTERVAL_MS = 200
TMP_FLUSH_INTERVAL_MS = 60_000
NO_DATA_TIMEOUT_S = 5.0
INFO_MAX_LEN = 30

MSG_NO_PORT = "Arduino が見つかりません。USB の接続を確認してください"
MSG_UNSAVED = "保存されていないデータがあります。保存しますか？"


class State(Enum):
    IDLE = "未測定"
    MEASURING = "測定中"
    STOPPED = "停止後"


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, mock: bool = False, mock_speed: float = 1.0) -> None:
        super().__init__()
        self.paths = paths
        self.mock = mock
        self.mock_speed = mock_speed
        self.settings = Settings.load(paths.settings_file)

        self.state = State.IDLE
        self.data: MeasurementData | None = None
        self.offset_mA: float | None = None  # ゼロ点補正値（アプリ終了まで保持）
        self.reader: SerialReader | None = None
        self.rx_queue: queue.Queue = queue.Queue()
        self.writer: RecoveryWriter | None = None
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None
        self.unsaved = False
        self.manual_name = False
        self.busy = False  # ゼロ点補正・保存の実行中
        self._device = ""
        self._tmp_submitted = 0
        self._tmp_error_shown = False
        self._last_data_mono = 0.0
        self._no_data_shown = False
        self._window_before_stop: float | None = DEFAULT_RANGE

        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 700)
        self._build_ui()

        self.board_edit.setText(self.settings.board)
        self.target_edit.setText(self.settings.target)
        self.operator_edit.setText(self.settings.operator)
        save_dir = self.settings.save_dir or str(default_save_dir())
        self.save_dir_edit.setText(save_dir)
        self.pc_time_check.setChecked(self.settings.include_pc_time)
        self._auto_name()

        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(UPDATE_INTERVAL_MS)
        self.tick_timer.timeout.connect(self._tick)
        self.tmp_timer = QTimer(self)
        self.tmp_timer.setInterval(TMP_FLUSH_INTERVAL_MS)
        self.tmp_timer.timeout.connect(self._flush_tmp)

        self.refresh_ports()
        self._update_numbers()
        self._update_status()

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 4)

        # ---- 上部ツールバー ----
        bar = QHBoxLayout()
        title = QLabel(APP_NAME)
        f = title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        title.setFont(f)
        bar.addWidget(title)
        self.measuring_label = QLabel("")
        self.measuring_label.setMinimumWidth(80)
        bar.addWidget(self.measuring_label)
        bar.addSpacing(16)
        bar.addWidget(QLabel("COM ポート"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(260)
        bar.addWidget(self.port_combo)
        self.refresh_button = QPushButton("更新")
        self.refresh_button.clicked.connect(self.refresh_ports)
        bar.addWidget(self.refresh_button)
        bar.addStretch(1)
        self.start_button = QPushButton("開始")
        self.stop_button = QPushButton("停止")
        self.save_button = QPushButton("保存")
        for b in (self.start_button, self.stop_button, self.save_button):
            b.setMinimumWidth(90)
            b.setMinimumHeight(32)
            bar.addWidget(b)
        self.start_button.clicked.connect(self.on_start)
        self.stop_button.clicked.connect(self.on_stop)
        self.save_button.clicked.connect(self.on_save)
        root.addLayout(bar)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        self.plot = PlotPanel()
        splitter.addWidget(self.plot)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 890])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

        # ---- 下部ステータスバー ----
        sb = QStatusBar()
        self.conn_label = QLabel()
        self.samples_label = QLabel()
        self.invalid_label = QLabel()
        self.tmp_label = QLabel()
        self.msg_label = QLabel()
        self.msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder_button = QPushButton("フォルダを開く")
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(self._open_saved_folder)
        for w in (self.conn_label, self.samples_label, self.invalid_label, self.tmp_label):
            w.setMinimumWidth(110)
            sb.addWidget(w)
        sb.addWidget(self.msg_label, 1)
        sb.addPermanentWidget(self.open_folder_button)
        self.setStatusBar(sb)
        self._saved_folder: Path | None = None

    def _build_left(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 6, 0)

        # 数値表示（F-04）
        g = QGroupBox("測定値")
        gl = QVBoxLayout(g)
        self.current_label = QLabel()
        big = QFont(self.current_label.font())
        big.setPointSize(30)
        big.setBold(True)
        self.current_label.setFont(big)
        self.current_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = QHBoxLayout()
        row.addWidget(QLabel("電流"), 0, Qt.AlignTop)
        row.addWidget(self.current_label, 1)
        gl.addLayout(row)
        form = QFormLayout()
        self.voltage_label = QLabel()
        self.power_label = QLabel()
        self.mah_label = QLabel()
        self.elapsed_label = QLabel()
        mid = QFont(self.voltage_label.font())
        mid.setPointSize(mid.pointSize() + 3)
        for text, w in (("電圧", self.voltage_label), ("電力", self.power_label),
                        ("積算", self.mah_label), ("経過時間", self.elapsed_label)):
            w.setFont(mid)
            w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.addRow(text, w)
        gl.addLayout(form)
        lay.addWidget(g)

        # ゼロ点補正（F-12）
        g = QGroupBox("ゼロ点補正")
        gl = QVBoxLayout(g)
        self.offset_label = QLabel()
        gl.addWidget(self.offset_label)
        row = QHBoxLayout()
        self.zero_button = QPushButton("ゼロ点補正")
        self.zero_reset_button = QPushButton("補正値をリセット")
        self.zero_button.clicked.connect(self.on_zero_cal)
        self.zero_reset_button.clicked.connect(self.on_zero_reset)
        row.addWidget(self.zero_button)
        row.addWidget(self.zero_reset_button)
        gl.addLayout(row)
        lay.addWidget(g)
        self._update_offset_label()

        # 測定情報（F-07）
        g = QGroupBox("測定情報")
        form = QFormLayout(g)
        self.board_edit = QLineEdit()
        self.target_edit = QLineEdit()
        self.operator_edit = QLineEdit()
        for w in (self.board_edit, self.target_edit, self.operator_edit):
            w.setMaxLength(INFO_MAX_LEN)
        self.board_edit.setPlaceholderText("例：MAIN-RevB")
        self.target_edit.setPlaceholderText("例：REG-U3")
        self.datetime_edit = QLineEdit()
        self.datetime_edit.setReadOnly(True)
        self.datetime_edit.setFocusPolicy(Qt.NoFocus)
        self.datetime_edit.setPlaceholderText("開始時に自動で入ります")
        form.addRow("基板情報", self.board_edit)
        form.addRow("測定対象", self.target_edit)
        form.addRow("測定者", self.operator_edit)
        form.addRow("測定日時", self.datetime_edit)
        lay.addWidget(g)

        # メモ
        g = QGroupBox("メモ（入力すると保存時に .txt も出力）")
        gl = QVBoxLayout(g)
        self.memo_edit = QPlainTextEdit()
        self.memo_edit.setMinimumHeight(56)
        gl.addWidget(self.memo_edit)
        lay.addWidget(g, 1)

        # 出力ファイル名と保存先（F-08）
        g = QGroupBox("出力")
        grid = QGridLayout(g)
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._on_name_edited)
        self.auto_name_button = QPushButton("自動生成")
        self.auto_name_button.clicked.connect(self.on_auto_name)
        grid.addWidget(QLabel("出力ファイル名"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(self.auto_name_button, 0, 2)
        hint = QLabel(".csv / .png（メモ入力時は .txt も）が付きます")
        hint.setStyleSheet("color: gray;")
        grid.addWidget(hint, 1, 1, 1, 2)
        self.save_dir_edit = QLineEdit()
        self.save_dir_edit.setReadOnly(True)
        self.browse_button = QPushButton("参照…")
        self.browse_button.clicked.connect(self.on_browse)
        grid.addWidget(QLabel("保存先"), 2, 0)
        grid.addWidget(self.save_dir_edit, 2, 1)
        grid.addWidget(self.browse_button, 2, 2)
        self.pc_time_check = QCheckBox("PC の日時を出力する")
        grid.addWidget(self.pc_time_check, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addWidget(g)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(380)
        scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        return scroll

    # ================================================================ 表示更新
    def _update_buttons(self) -> None:
        has_port = bool(self.port_combo.currentData())
        idle_like = self.state in (State.IDLE, State.STOPPED) and not self.busy
        self.start_button.setEnabled(idle_like and has_port)
        self.stop_button.setEnabled(self.state is State.MEASURING and not self.busy)
        self.save_button.setEnabled(self.state is State.STOPPED and not self.busy)
        self.zero_button.setEnabled(idle_like and has_port)
        self.zero_reset_button.setEnabled(idle_like)
        self.port_combo.setEnabled(idle_like)
        self.refresh_button.setEnabled(idle_like)
        self.browse_button.setEnabled(not self.busy)
        if self.state is State.MEASURING:
            self.measuring_label.setText("● 測定中")
            self.measuring_label.setStyleSheet("color: #C00000; font-weight: bold;")
        else:
            self.measuring_label.setText("停止中" if self.state is State.STOPPED else "")
            self.measuring_label.setStyleSheet("color: gray;")

    def _update_numbers(self) -> None:
        last = self.data.last() if self.data else None
        if last is None:
            self.current_label.setText("--- mA")
            for w in (self.voltage_label, self.power_label, self.mah_label):
                w.setText("---")
            self.elapsed_label.setText(format_hms(0))
            return
        self.current_label.setText(f"{last['current_mA']:.3f} mA")
        self.voltage_label.setText(f"{last['bus_V']:.4f} V")
        self.power_label.setText(f"{last['power_mW']:.3f} mW")
        self.mah_label.setText(f"{self.data.mAh:.4f} mAh")
        self.elapsed_label.setText(format_hms(self.data.last_elapsed_s))

    def _update_status(self) -> None:
        if self.state is State.MEASURING:
            self.conn_label.setText(f"接続中：{self._device}")
        else:
            self.conn_label.setText("未接続")
        n = self.data.count if self.data else 0
        bad = self.data.invalid_count if self.data else 0
        self.samples_label.setText(f"受信サンプル数 {n}")
        self.invalid_label.setText(f"不正行数 {bad}")
        last_write = self.writer.status()[0] if self.writer else None
        t = time.strftime("%H:%M:%S", time.localtime(last_write)) if last_write else "--:--:--"
        self.tmp_label.setText(f"一時ファイル {t}")

    def _update_offset_label(self) -> None:
        self.offset_label.setText(f"ゼロ点補正値 {format_offset(self.offset_mA)}")

    def set_message(self, text: str) -> None:
        self.msg_label.setText(text)
        if text:
            self.open_folder_button.setVisible(False)

    # ================================================================ F-01 ポート
    def refresh_ports(self) -> None:
        current = self.port_combo.currentData() or self.settings.last_port
        self.port_combo.clear()
        ports = list_ports(include_mock=self.mock)
        for p in ports:
            self.port_combo.addItem(p.label, p.device)
        if not ports:
            self.port_combo.addItem("（ポートがありません）", "")
            self.set_message(MSG_NO_PORT)
        else:
            idx = self.port_combo.findData(current)
            self.port_combo.setCurrentIndex(max(idx, 0))
            if self.msg_label.text() == MSG_NO_PORT:
                self.set_message("")
        self._update_buttons()

    # ================================================================ F-07 / F-08 ファイル名
    def _info(self) -> MeasurementInfo:
        return MeasurementInfo(
            board=self.board_edit.text().strip(),
            target=self.target_edit.text().strip(),
            operator=self.operator_edit.text().strip(),
            started_at=self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else "",
            ended_at=self.ended_at.strftime("%Y-%m-%d %H:%M:%S") if self.ended_at else "",
            memo=self.memo_edit.toPlainText(),
        )

    def _auto_name(self) -> None:
        self.name_edit.setText(
            build_base_name(self.started_at, self.target_edit.text(), self.board_edit.text(), self.operator_edit.text())
        )
        self.manual_name = False

    def on_auto_name(self) -> None:
        self._auto_name()

    def _on_name_edited(self, _text: str) -> None:
        self.manual_name = True

    def on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダ", self.save_dir_edit.text())
        if folder:
            self.save_dir_edit.setText(folder)
            self.settings.save_dir = folder
            self._save_settings()

    def _save_settings(self) -> None:
        s = self.settings
        if self.port_combo.currentData():
            s.last_port = self.port_combo.currentData()
        s.board = self.board_edit.text()
        s.target = self.target_edit.text()
        s.operator = self.operator_edit.text()
        s.save_dir = self.save_dir_edit.text()
        s.include_pc_time = self.pc_time_check.isChecked()
        s.save(self.paths.settings_file)

    # ================================================================ F-02 開始
    def on_start(self) -> None:
        if self.state is State.MEASURING or self.busy:
            return
        if not self._confirm_unsaved():
            return
        device = self.port_combo.currentData()
        if not device:
            self.set_message(MSG_NO_PORT)
            return
        try:
            port = open_port(device, self.mock_speed)
        except PortOpenError as e:
            self.set_message(str(e))
            QMessageBox.warning(self, APP_NAME, str(e))
            return

        self._discard_tmp()
        self._device = device
        self.started_at = datetime.now().replace(microsecond=0)
        self.ended_at = None
        self.datetime_edit.setText(self.started_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.memo_edit.clear()
        if not self.manual_name:
            self._auto_name()
        self.data = MeasurementData(self.offset_mA)
        self.unsaved = False
        info = self._info()
        self.writer = RecoveryWriter(
            self.paths.recovery_dir,
            stamp(self.started_at),
            {"board": info.board, "target": info.target, "operator": info.operator, "started_at": info.started_at},
        )
        self._tmp_submitted = 0
        self._tmp_error_shown = False
        self._last_data_mono = time.monotonic()
        self._no_data_shown = False

        self.rx_queue = queue.Queue()
        self.reader = SerialReader(port, self.rx_queue)
        self.reader.start()
        self.state = State.MEASURING
        self.set_message("測定を開始しました")
        self.plot.set_data(self.data, live=True)
        self.plot.set_window(self._window_before_stop)
        self.tick_timer.start()
        self.tmp_timer.start()
        self._save_settings()
        self._update_buttons()
        self._update_numbers()
        self._update_status()

    def _drain(self) -> tuple[bool, str | None]:
        """受信キューを空にしてデータに取り込む。(リセットがあったか, 切断メッセージ)"""
        reset = False
        disconnected = None
        d = self.data
        while True:
            try:
                ev = self.rx_queue.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "line":
                parsed, pc_time = ev[1], ev[2]
                if parsed.kind is LineKind.DATA:
                    r = parsed.row
                    if d.add(r.time_ms, r.bus_V, r.current_mA, pc_time).reset_detected:
                        reset = True
                    self._last_data_mono = time.monotonic()
                elif parsed.kind is LineKind.COMMENT:
                    self.set_message(parsed.text)
                elif parsed.kind is LineKind.INVALID:
                    d.invalid_count += 1
            elif ev[0] == "disconnected":
                disconnected = ev[1]
        return reset, disconnected

    def _tick(self) -> None:
        if self.state is not State.MEASURING:
            return
        n_before = self.data.count
        reset, disconnected = self._drain()
        if reset:
            log.warning("Arduino がリセットされました（time_ms が減少）")
            self.set_message("Arduino がリセットされました")
        if self.data.count > n_before and self._no_data_shown:
            self._no_data_shown = False
            self.set_message("")
        if time.monotonic() - self._last_data_mono > NO_DATA_TIMEOUT_S and not self._no_data_shown:
            self._no_data_shown = True
            log.warning("データが届いていません（%s）", self._device)
            self.set_message("データが届いていません")
        self._check_tmp_status()
        self._update_numbers()
        self.plot.refresh()
        self._update_status()
        if disconnected is not None:
            self._stop_measurement()
            msg = "Arduino との接続が切れました"
            self.set_message(msg)
            QMessageBox.warning(self, APP_NAME, msg)

    # ================================================================ F-10 一時ファイル
    def _flush_tmp(self) -> None:
        if self.writer is None or self.data is None:
            return
        if self.state is State.MEASURING:
            self._drain_into_data_only()
        n = self.data.count
        if n > self._tmp_submitted:
            self.writer.submit(self.data.slice_copy(self._tmp_submitted, n))
            self._tmp_submitted = n

    def _drain_into_data_only(self) -> None:
        # 一時ファイルに書く直前に受信済みの行を取り込む（表示は次の _tick で更新）
        reset, disconnected = self._drain()
        if reset:
            self.set_message("Arduino がリセットされました")
        if disconnected is not None:
            self.rx_queue.put(("disconnected", disconnected))

    def _check_tmp_status(self) -> None:
        if self.writer is None:
            return
        _, error, _ = self.writer.status()
        if error and not self._tmp_error_shown:
            self._tmp_error_shown = True
            self.set_message("一時ファイルに書き込めません")
        elif not error and self._tmp_error_shown:
            self._tmp_error_shown = False

    def _discard_tmp(self) -> None:
        if self.writer is not None:
            self.writer.delete_files()
            self.writer = None

    # ================================================================ F-06 停止
    def on_stop(self) -> None:
        if self.state is not State.MEASURING:
            return
        self._stop_measurement()
        self.set_message("測定を停止しました")

    def _stop_measurement(self) -> None:
        self.tick_timer.stop()
        self.tmp_timer.stop()
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        reset, _ = self._drain()
        if reset:
            self.set_message("Arduino がリセットされました")
        self.ended_at = datetime.now().replace(microsecond=0)
        self.state = State.STOPPED
        if self.writer is not None:
            self._flush_tmp()
            if not self.writer.close():
                self.set_message("一時ファイルに書き込めません")
        self.unsaved = self.data is not None and self.data.count > 0
        if not self.unsaved:
            self._discard_tmp()
        self._window_before_stop = self.plot.window
        self.plot.set_live(False)
        self.plot.set_window(None)
        self._update_buttons()
        self._update_numbers()
        self._update_status()

    # ================================================================ F-08 保存
    def on_save(self) -> bool:
        if self.state is not State.STOPPED or self.data is None or self.busy:
            return False
        base = sanitize(self.name_edit.text().strip())
        if not base:
            msg = "出力ファイル名を入力してください"
            self.set_message(msg)
            QMessageBox.warning(self, APP_NAME, msg)
            return False
        if base != self.name_edit.text():
            self.name_edit.setText(base)
        folder = Path(self.save_dir_edit.text() or default_save_dir())
        info = self._info()
        self._save_settings()

        self.busy = True
        self._update_buttons()
        progress = QProgressDialog("保存中…", "", 0, 100, self)
        progress.setWindowTitle(APP_NAME)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        def on_progress(done: int, total: int) -> None:
            progress.setValue(10 + int(85 * done / max(total, 1)))
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        try:
            png = image_to_png_bytes(render_image(self.data, build_title(info), build_note(self.data)))
            progress.setValue(10)
            folder.mkdir(parents=True, exist_ok=True)
            result = save_measurement(
                folder, base, self.data, info, self.pc_time_check.isChecked(), png, on_progress
            )
        except (SaveError, OSError) as e:
            progress.close()
            self.busy = False
            self._update_buttons()
            msg = str(e) if isinstance(e, SaveError) else f"{folder} に保存できません（{e}）"
            log.warning("保存失敗: %s", msg)
            self.set_message(msg)
            QMessageBox.warning(self, APP_NAME, msg)
            return False
        progress.setValue(100)
        progress.close()
        self.busy = False

        self._discard_tmp()
        self.unsaved = False
        msg = f"保存しました：{result.folder}"
        if result.base != base:
            msg += f"（同名のファイルがあったため {result.base} として保存）"
        self.set_message(msg)
        self._saved_folder = result.folder
        self.open_folder_button.setVisible(True)
        self._update_buttons()
        self._update_status()
        return True

    def _open_saved_folder(self) -> None:
        if self._saved_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved_folder)))

    # ================================================================ F-09 未保存データ
    def _ask_unsaved(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Warning)
        box.setText(MSG_UNSAVED)
        save = box.addButton("保存", QMessageBox.AcceptRole)
        discard = box.addButton("保存しない", QMessageBox.DestructiveRole)
        cancel = box.addButton("キャンセル", QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is discard else "cancel"

    def _confirm_unsaved(self) -> bool:
        """未保存データがあれば確認する。続けてよければ True。"""
        if not self.unsaved:
            return True
        ans = self._ask_unsaved()
        if ans == "save":
            return self.on_save()
        if ans == "discard":
            self._discard_tmp()
            self.unsaved = False
            return True
        return False

    def closeEvent(self, event) -> None:
        if self.busy:
            event.ignore()
            return
        if self.state is State.MEASURING or self.unsaved:
            ans = self._ask_unsaved()
            if ans == "cancel":
                event.ignore()
                return
            if self.state is State.MEASURING:
                self._stop_measurement()
            if ans == "save" and self.unsaved and not self.on_save():
                event.ignore()
                return
            if ans == "discard":
                self._discard_tmp()
                self.unsaved = False
        self._save_settings()
        event.accept()

    # ================================================================ F-12 ゼロ点補正
    def on_zero_cal(self) -> None:
        if self.state is State.MEASURING or self.busy:
            return
        device = self.port_combo.currentData()
        if not device:
            return
        ok = QMessageBox.question(
            self, APP_NAME, "負荷を外してから OK を押してください",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok,
        )
        if ok != QMessageBox.Ok:
            return
        try:
            port = open_port(device, self.mock_speed)
        except PortOpenError as e:
            self.set_message(str(e))
            QMessageBox.warning(self, APP_NAME, str(e))
            return

        self.busy = True
        self._update_buttons()
        stop_event = threading.Event()
        result: dict = {}

        def work() -> None:
            try:
                result["offset"] = compute_offset(collect_samples(port, stop_event=stop_event))
            except ZeroCalError as e:
                result["error"] = e
            except Exception as e:  # 想定外でも GUI を止めない
                log.warning("ゼロ点補正で例外", exc_info=True)
                result["error"] = ZeroCalError(ZeroCalFailure.NO_DATA)
                result["exc"] = e
            finally:
                try:
                    port.close()
                except Exception:
                    pass

        th = threading.Thread(target=work, name="zero-cal", daemon=True)
        th.start()
        progress = QProgressDialog("ゼロ点補正中…（データを 20 行受信しています）", "キャンセル", 0, 0, self)
        progress.setWindowTitle(APP_NAME)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(stop_event.set)
        progress.show()
        while th.is_alive():
            QApplication.processEvents(QEventLoop.AllEvents, 50)
            th.join(0.02)
        progress.close()
        self.busy = False
        self._update_buttons()

        if "offset" in result:
            self.offset_mA = result["offset"]
            self._update_offset_label()
            self.set_message(f"ゼロ点補正値 {format_offset(self.offset_mA)} を記録しました")
            return
        err: ZeroCalError = result["error"]
        if err.failure is ZeroCalFailure.CANCELLED:
            self.set_message("ゼロ点補正をキャンセルしました")
            return
        msg = err.failure.value
        if err.failure is ZeroCalFailure.LOAD_DETECTED and err.value is not None:
            msg += f"（平均 {err.value:.3f} mA）"
        self.set_message(msg)
        QMessageBox.warning(self, APP_NAME, msg)

    def on_zero_reset(self) -> None:
        self.offset_mA = None
        self._update_offset_label()
        self.set_message("ゼロ点補正値をリセットしました")

    # ================================================================ F-11 復旧
    def check_recovery(self) -> None:
        for item in find_recoverable(self.paths.recovery_dir):
            target = str(item.info.get("target", "") or "")
            when = str(item.info.get("started_at", "") or item.stamp)
            parts = [p for p in (target, when, f"{item.row_count} 行") if p]
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Question)
            box.setText(f"前回の測定データが残っています（{'、'.join(parts)}）。保存しますか？")
            save = box.addButton("保存", QMessageBox.AcceptRole)
            delete = box.addButton("削除", QMessageBox.DestructiveRole)
            later = box.addButton("あとで", QMessageBox.RejectRole)
            box.setEscapeButton(later)
            box.exec()
            clicked = box.clickedButton()
            if clicked is save:
                folder = QFileDialog.getExistingDirectory(self, "復旧したデータの保存先", self.save_dir_edit.text())
                if not folder:
                    continue
                try:
                    dest = recover_to(item, Path(folder))
                except OSError as e:
                    log.warning("復旧に失敗", exc_info=True)
                    QMessageBox.warning(self, APP_NAME, f"{folder} に保存できません（{e}）")
                    continue
                delete_item(item)
                self.set_message(f"復旧しました：{dest}")
                self._saved_folder = dest.parent
                self.open_folder_button.setVisible(True)
            elif clicked is delete:
                delete_item(item)

    # ================================================================ 開発用
    def load_mock_preload(self, n: int) -> None:
        """模擬データ n サンプルを「停止後」の状態で読み込む（AC-13 の手動確認用）。"""
        t0 = time.time() - n * 0.05
        d = MeasurementData(self.offset_mA)
        for t, line in generate_lines(n, MockProfile()):
            p = parse_line(line)
            if p.kind is LineKind.DATA:
                d.add(p.row.time_ms, p.row.bus_V, p.row.current_mA, t0 + t)
            elif p.kind is LineKind.INVALID:
                d.invalid_count += 1
        self.data = d
        self.started_at = datetime.fromtimestamp(t0).replace(microsecond=0)
        self.ended_at = datetime.now().replace(microsecond=0)
        self.datetime_edit.setText(self.started_at.strftime("%Y-%m-%d %H:%M:%S"))
        self._auto_name()
        self.state = State.STOPPED
        self.unsaved = True
        self.plot.set_data(d, live=False)
        self.plot.set_window(None)
        self._update_buttons()
        self._update_numbers()
        self._update_status()
        self.set_message(f"模擬データ {n} サンプルを読み込みました")
