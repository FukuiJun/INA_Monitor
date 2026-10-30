"""起動処理。"""

from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__
from .logsetup import setup_logging
from .paths import AppPaths


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=APP_NAME, description="INA226 電流・電圧モニタ")
    ap.add_argument("--mock", action="store_true", help="模擬シリアル（MOCK1〜4）をポート一覧に出す")
    ap.add_argument("--mock-speed", type=float, default=1.0, help="模擬シリアルの速度倍率（開発用）")
    ap.add_argument("--mock-preload", type=int, default=0, metavar="N",
                    help="模擬データ N サンプルを停止後の状態で読み込む（例：576000）")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} v{__version__}")
    return ap.parse_args(argv)


APP_USER_MODEL_ID = f"FukuiJun.{APP_NAME}"


def set_app_user_model_id() -> None:
    """タスクバーに python.exe ではなくこのアプリのアイコンを出すための Windows の設定。

    ID に版を入れると、版が変わるたびに Windows が別のアプリとして扱い、
    タスクバーにピン留めしたボタンと起動中のボタンが分かれてしまうので、版は入れない。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    paths = AppPaths.default()
    setup_logging(paths.log_file)

    set_app_user_model_id()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow
    from . import theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    app.setWindowIcon(theme.app_icon())
    mock = args.mock or args.mock_preload > 0
    win = MainWindow(paths, mock=mock, mock_speed=args.mock_speed)
    win.show()
    if args.mock_preload > 0:
        win.load_mock_preload(args.mock_preload)
    QTimer.singleShot(0, win.check_recovery)
    return app.exec()
