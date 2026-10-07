"""PyInstaller entry point (absolute imports; the package's __main__ is not run as a package when frozen)."""

import sys

from takeline.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
