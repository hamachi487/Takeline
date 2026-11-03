#!/usr/bin/env bash
# Builds dist/takeline, a self-contained command-line binary for Linux.
#
#   ./build_linux.sh               # run tests, then build
#   ./build_linux.sh --skip-tests
#
# Needs Python 3.10+ with venv on the build machine. The binary runs on Linux
# systems with the same or newer glibc. ExifTool is used if found on PATH.
set -euo pipefail
cd "$(dirname "$0")"

skip_tests=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) skip_tests=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

python="${PYTHON:-python3}"
[ -d .venv-cli ] || "$python" -m venv .venv-cli
py=.venv-cli/bin/python
"$py" -m pip install --upgrade pip --quiet
"$py" -m pip install "tzdata>=2024.1" "pytest>=8" "pyinstaller>=6.6" --quiet

if [ "$skip_tests" -eq 0 ]; then
    echo "Running tests..."
    "$py" -m pytest
fi

echo "Building takeline..."
"$py" -m PyInstaller --noconfirm --clean takeline-cli.spec
echo "Built: dist/takeline"
