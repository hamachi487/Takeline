# PyInstaller spec for the command-line `takeline` binary (Linux; no Qt).
# Build with:  pyinstaller --noconfirm takeline-cli.spec   (build_linux.sh does this for you)
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
datas = collect_data_files("tzdata")
datas.append((str(ROOT / "THIRD-PARTY-NOTICES.md"), "."))

a = Analysis(
    [str(ROOT / "src" / "takeline_cli.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=["zoneinfo", "tzdata"],
    excludes=["PySide6", "shiboken6", "takeline.gui", "takeline.app", "tkinter", "unittest", "pydoc", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="takeline",
    console=True,
    upx=False,
)
