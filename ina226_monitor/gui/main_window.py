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
from PySide6.QtGui import QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QMenu,
    QScrollArea,
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
from ..theme import mono_font, ui_font
from .plot_panel import DEFAULT_RANGE, PlotPanel
from .widgets import FitLabel, SaveOptionsDialog, divider, sub_label

log = logging.getLogger(__name__)

UPDATE_INTERVAL_MS = 200
TMP_FLUSH_INTERVAL_MS = 60_000
NO_DATA_TIMEOUT_S = 5.0
INFO_MAX_LEN = 30
LEFT_PANEL_WIDTH = 320

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
        if not self.settings.save_dir:
            self.settings.save_dir = str(default_save_dir())
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
        if not self.msg_label.text():
            self.set_message("準備完了")

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        body.addWidget(self._build_left())  # 幅固定
        right = QVBoxLayout()
        right.setSpacing(12)
        self.plot = PlotPanel()
        right.addWidget(self.plot, 1)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.start_button = self._action_button("▶ 開始", "startButton", self.on_start)
        self.stop_button = self._action_button("■ 停止", "stopButton", self.on_stop)
        self.save_button = self._action_button("保存", "saveButton", self.on_save)
        for b in (self.start_button, self.stop_button, self.save_button):
            actions.addWidget(b)
        right.addLayout(actions)
        body.addLayout(right, 1)  # 広げたときはグラフ側が広がる
        root.addLayout(body, 1)
        self.setCentralWidget(central)
        self._build_status_bar()

    def _action_button(self, text: str, name: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setFont(ui_font(18, bold=True))
        b.setMinimumSize(140, 52)
        b.clicked.connect(slot)
        return b

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)  # 下端の帯 4px を含む
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 8, 16, 12)
        lay.setSpacing(10)
        decor = QHBoxLayout()
        decor.setSpacing(3)
        for _ in range(4):  # 端子台を模した飾り
            bar = QFrame()
            bar.setObjectName("headerDecor")
            bar.setFixedSize(5, 20)
            decor.addWidget(bar)
        lay.addLayout(decor)
        name = QLabel(APP_NAME)
        name.setObjectName("appName")
        name.setFont(ui_font(19, bold=True))
        lay.addWidget(name)
        self.measuring_badge = QLabel("● 測定中")
        self.measuring_badge.setObjectName("measuringBadge")
        self.measuring_badge.setFont(ui_font(12, bold=True))
        self.measuring_badge.setVisible(False)
        lay.addWidget(self.measuring_badge)
        lay.addStretch(1)
        port_label = QLabel("ポート")
        port_label.setObjectName("portLabel")
        port_label.setFont(ui_font(13))
        lay.addWidget(port_label)
        self.port_combo = QComboBox()
        self.port_combo.setFont(ui_font(14))
        self.port_combo.setMinimumWidth(220)
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        lay.addWidget(self.port_combo)
        self.refresh_button = QPushButton("更新")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFont(ui_font(14))
        self.refresh_button.clicked.connect(self.refresh_ports)
        lay.addWidget(self.refresh_button)
        return header

    def _build_left(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 電流（見出しと経過時間）
        row = QHBoxLayout()
        heading = QLabel("電流")
        heading.setFont(ui_font(13, bold=True))
        row.addWidget(heading)
        row.addStretch(1)
        self.elapsed_label = QLabel()
        self.elapsed_label.setProperty("role", "sub")
        self.elapsed_label.setFont(mono_font(13))
        row.addWidget(self.elapsed_label)
        lay.addLayout(row)

        # 電流の数値バッジ
        badge = QFrame()
        badge.setObjectName("currentBadge")
        bl = QHBoxLayout(badge)
        bl.setContentsMargins(14, 4, 14, 4)
        bl.setSpacing(8)
        self.current_label = FitLabel(mono_font(46, bold=True), min_px=24)
        bl.addWidget(self.current_label, 1)
        unit = QLabel("mA")
        unit.setFont(ui_font(20, bold=True))
        bl.addWidget(unit, 0, Qt.AlignBottom)
        lay.addWidget(badge)

        # 電圧・電力・積算
        grid = QFrame()
        grid.setObjectName("valueGrid")
        gl = QHBoxLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(0)
        cells = []
        for i, title in enumerate(("電圧 V", "電力 mW", "積算 mAh")):
            cell = QFrame()
            cell.setObjectName("valueCell")
            cell.setProperty("divider", i < 2)
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 6, 8, 6)
            cl.setSpacing(0)
            cl.addWidget(sub_label(title, 12))
            value = FitLabel(mono_font(18, bold=True), min_px=11)
            value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            cl.addWidget(value)
            gl.addWidget(cell, 1)
            cells.append(value)
        self.voltage_label, self.power_label, self.mah_label = cells
        lay.addWidget(grid)

        # ゼロ点補正（リセットは右クリックメニュー）
        row = QHBoxLayout()
        self.offset_label = sub_label("", 12)
        row.addWidget(self.offset_label)
        row.addStretch(1)
        self.zero_button = QPushButton("ゼロ点補正")
        self.zero_button.setObjectName("zeroButton")
        self.zero_button.setFont(ui_font(13))
        self.zero_button.setMinimumHeight(32)
        self.zero_button.setToolTip("右クリックで「補正値をリセット」")
        self.zero_button.clicked.connect(self.on_zero_cal)
        self.zero_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zero_button.customContextMenuRequested.connect(self._zero_menu)
        row.addWidget(self.zero_button)
        lay.addLayout(row)
        self._update_offset_label()

        lay.addWidget(divider())

        # 測定情報（F-07）
        heading = QLabel("測定情報")
        heading.setFont(ui_font(13, bold=True))
        lay.addWidget(heading)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.board_edit = QLineEdit()
        self.target_edit = QLineEdit()
        self.operator_edit = QLineEdit()
        for w in (self.board_edit, self.target_edit, self.operator_edit):
            w.setMaxLength(INFO_MAX_LEN)
            w.setFont(ui_font(14))
        self.datetime_edit = QLineEdit()
        self.datetime_edit.setReadOnly(True)
        self.datetime_edit.setFocusPolicy(Qt.NoFocus)
        self.datetime_edit.setFont(mono_font(13))
        self.datetime_edit.setToolTip("開始ボタンで更新されます")
        for r, (text, w) in enumerate((("基板情報", self.board_edit), ("測定対象", self.target_edit),
                                        ("測定者", self.operator_edit), ("測定日時", self.datetime_edit))):
            w.setFixedHeight(32)
            form.addWidget(sub_label(text), r, 0)
            form.addWidget(w, r, 1)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)

        lay.addWidget(divider())

        # メモ
        memo_label = sub_label("メモ <span style='font-size:11px'>（入力すると .txt も出力）</span>")
        lay.addWidget(memo_label)
        self.memo_edit = QPlainTextEdit()
        self.memo_edit.setFont(ui_font(14))
        line_h = QFontMetrics(self.memo_edit.font()).lineSpacing()
        self.memo_edit.setFixedHeight(line_h * 2 + 18)  # 2 行
        lay.addWidget(self.memo_edit)

        # 出力ファイル名（F-08）
        lay.addWidget(sub_label("出力ファイル名 <span style='font-size:11px'>（.csv / .png / .txt）</span>"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.name_edit = QLineEdit()
        self.name_edit.setFont(mono_font(12))
        self.name_edit.setFixedHeight(32)
        self.name_edit.textEdited.connect(self._on_name_edited)
        self.auto_name_button = QPushButton("自動生成")
        self.auto_name_button.setFont(ui_font(12))
        self.auto_name_button.setFixedHeight(32)
        self.auto_name_button.clicked.connect(self.on_auto_name)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.auto_name_button)
        lay.addLayout(row)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("leftScroll")
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(LEFT_PANEL_WIDTH)
        return scroll

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.conn_label = QLabel()
        self.samples_label = QLabel()
        self.invalid_label = QLabel()
        self.tmp_label = QLabel()
        self.msg_label = QLabel()
        self.msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder_button = QPushButton("フォルダを開く")
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(self._open_saved_folder)
        for w in (self.conn_label, self.samples_label, self.invalid_label, self.tmp_label,
                  self.msg_label, self.open_folder_button):
            w.setFont(ui_font(12))
        for w in (self.conn_label, self.samples_label, self.invalid_label, self.tmp_label):
            sb.addWidget(w)
        sb.addPermanentWidget(self.msg_label)  # メッセージは右寄せ
        sb.addPermanentWidget(self.open_folder_button)
        self.setStatusBar(sb)
        self._saved_folder: Path | None = None

    # ================================================================ 表示更新
    def _update_buttons(self) -> None:
        has_port = bool(self.port_combo.currentData())
        idle_like = self.state in (State.IDLE, State.STOPPED) and not self.busy
        self.start_button.setEnabled(idle_like and has_port)
        self.stop_button.setEnabled(self.state is State.MEASURING and not self.busy)
        self.save_button.setEnabled(self.state is State.STOPPED and not self.busy)
        self.zero_button.setEnabled(idle_like and has_port)
        self.port_combo.setEnabled(idle_like)
        self.refresh_button.setEnabled(idle_like)
        self.measuring_badge.setVisible(self.state is State.MEASURING)

    def _update_numbers(self) -> None:
        last = self.data.last() if self.data else None
        if last is None:
            self.current_label.setText("---")
            for w in (self.voltage_label, self.power_label, self.mah_label):
                w.setText("---")
            self.elapsed_label.setText(f"経過 {format_hms(0)}")
            return
        self.current_label.setText(f"{last['current_mA']:.3f}")
        self.voltage_label.setText(f"{last['bus_V']:.4f}")
        self.power_label.setText(f"{last['power_mW']:.3f}")
        self.mah_label.setText(f"{self.data.mAh:.4f}")
        self.elapsed_label.setText(f"経過 {format_hms(self.data.last_elapsed_s)}")

    def _update_status(self) -> None:
        if self.state is State.MEASURING:
            self.conn_label.setText(f"接続中：{self._device}")
        else:
            self.conn_label.setText("未接続")
        n = self.data.count if self.data else 0
        bad = self.data.invalid_count if self.data else 0
        self.samples_label.setText(f"受信サンプル：{n:,}")
        self.invalid_label.setText(f"不正行：{bad:,}")
        last_write = self.writer.status()[0] if self.writer else None
        t = time.strftime("%H:%M:%S", time.localtime(last_write)) if last_write else "--:--:--"
        self.tmp_label.setText(f"一時保存：{t}")

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

    def _save_settings(self) -> None:
        s = self.settings
        if self.port_combo.currentData():
            s.last_port = self.port_combo.currentData()
        s.board = self.board_edit.text()
        s.target = self.target_edit.text()
        s.operator = self.operator_edit.text()
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
        info = self._info()
        options = self._ask_save_options(base, info.has_memo)
        if options is None:
            return False
        folder, include_pc_time = options
        self.settings.save_dir = str(folder)
        self.settings.include_pc_time = include_pc_time
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
                folder, base, self.data, info, include_pc_time, png, on_progress
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

    def _ask_save_options(self, base: str, with_txt: bool) -> tuple[Path, bool] | None:
        """保存先フォルダと PC 日時の出力を選ぶ。キャンセルなら None。"""
        dlg = SaveOptionsDialog(
            self, self.settings.save_dir or str(default_save_dir()), self.settings.include_pc_time, base, with_txt
        )
        if dlg.exec() != QDialog.Accepted:
            return None
        return Path(dlg.folder), dlg.include_pc_time

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

    def _zero_menu(self, pos) -> None:
        menu = QMenu(self)
        action = menu.addAction("補正値をリセット")
        action.setEnabled(self.offset_mA is not None and self.state is not State.MEASURING and not self.busy)
        action.triggered.connect(self.on_zero_reset)
        menu.exec(self.zero_button.mapToGlobal(pos))

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
                folder = QFileDialog.getExistingDirectory(
                    self, "復旧したデータの保存先", self.settings.save_dir or str(default_save_dir())
                )
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
