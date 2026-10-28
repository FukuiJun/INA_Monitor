# PyInstaller 設定（フォルダ配布）。build.bat から使う。
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["run_monitor.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["serial.tools.list_ports"],
    hookspath=[],
    runtime_hooks=[],
    # 使わない Qt バインディングや大きなモジュールは入れない
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "scipy", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="INA226_Monitor",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="INA226_Monitor",
)
