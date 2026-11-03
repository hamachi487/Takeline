"""PyInstaller entry point for the command-line build (no GUI, no Qt)."""

import sys

from takeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
