"""Worker entry point, started as ``python -I bootstrap.py <config.json> [observe|direct]``.

Isolated mode (-I) ignores PYTHONPATH and the user site directory and does not
put this file's directory on sys.path. The two entries added here are the
laboratory root (it holds only the ``rnglab_gui`` package) and the code
source's ``src``; RNGLab is therefore imported from that checkout only.
"""
import json
import sys
from pathlib import Path

GUI_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    config = json.loads(Path(sys.argv[1]).read_text())
    mode = sys.argv[2] if len(sys.argv) > 2 else "observe"
    code_src = str(Path(config["code_source"]["path"]) / "src")
    sys.path[:0] = [str(GUI_ROOT), code_src]
    if mode == "study":
        from rnglab_gui.worker.studies import main as study_main
        return study_main(config)
    from rnglab_gui.worker.runtime import run
    return run(config, mode)


if __name__ == "__main__":
    sys.exit(main())
