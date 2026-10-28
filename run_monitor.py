"""PyInstaller 用のエントリポイント。"""

from ina226_monitor.app import main

if __name__ == "__main__":
    raise SystemExit(main())
