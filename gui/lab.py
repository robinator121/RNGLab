#!/usr/bin/env python3
"""Start the RNGLab Laboratory from any directory.

    .venv/bin/python gui/lab.py [--port 8765] [--open] [--data-dir DIR] [--source CHECKOUT]

Runs are stored in gui/data/ unless --data-dir is given. See gui/README.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rnglab_gui.__main__ import main  # noqa: E402

sys.exit(main())
