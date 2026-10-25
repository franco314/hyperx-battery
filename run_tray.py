"""Launcher: PyInstaller entry point and target of the autostart Run key.

Equivalent to ``python -m hyperx_battery`` but works from any working
directory because it puts the project folder on ``sys.path`` itself.
"""

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hyperx_battery.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
