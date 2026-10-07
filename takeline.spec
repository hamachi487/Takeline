# PyInstaller spec for Takeline.exe
# Build with:  pyinstaller --noconfirm takeline.spec   (build_windows.bat does this for you)
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
datas = collect_data_files("tzdata")          # zoneinfo database (Windows has none built in)
datas.append((str(ROOT / "THIRD-PARTY-NOTICES.md"), "."))

# Optional: a bundled ExifTool (vendor/exiftool/exiftool.exe + exiftool_files/).
# The app works without it; when present it is found automatically at runtime.
vendor = ROOT / "vendor" / "exiftool"
if (vendor / "exiftool.exe").exists():
    for f in vendor.rglob("*"):
        if f.is_file():
            datas.append((str(f), str(Path("vendor/exiftool") / f.relative_to(vendor).parent)))

a = Analysis(
    [str(ROOT / "src" / "takeline_launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=["takeline.app", "takeline.cli", "zoneinfo", "tzdata"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtPdf",
              "PySide6.QtSql", "PySide6.QtTest", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Takeline",
    console=False,                 # GUI app: no console window
    icon=str(ROOT / "assets" / "Takeline.ico"),
    version=str(ROOT / "installer" / "version_info.txt"),
    upx=False,                     # UPX triggers more antivirus false positives
    disable_windowed_traceback=False,
)
