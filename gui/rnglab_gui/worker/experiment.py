"""Worker entry point for compound experiments: executes the shared ``rnglab_core`` API.

    python -I experiment.py catalog  <config.json>   -> registry of sources, faults, models (one JSON line)
    python -I experiment.py validate <config.json>   -> validation report (one JSON line)
    python -I experiment.py run      <config.json>   -> progress events (JSON lines), then a final line
    python -I experiment.py replay   <config.json>   -> replay of a stored record, then a comparison line

``config.json`` names the code source (``code_root``) whose ``src`` is put on ``sys.path``; the
server never imports scientific code. During ``run`` a line ``{"cmd": "cancel"}`` on stdin stops
the engine between units; the partial record is kept and sealed as cancelled.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, allow_nan=False, default=str) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode, config_path = sys.argv[1], Path(sys.argv[2])
    config = json.loads(config_path.read_text())
    sys.path.insert(0, str(Path(config["code_root"]) / "src"))
    from rnglab_core import engine, faults, models, sources
    from rnglab_core.record import _jsonable
    from rnglab_core.spec import ExperimentSpec, validate

    if mode == "catalog":
        emit({"t": "catalog", "sources": sources.describe(), "faults": faults.describe(),
              "not_integrated": faults.NOT_INTEGRATED,
              "models": [m.describe() for m in models.MODELS.values()]})
        return 0
    if mode == "validate":
        try:
            spec = ExperimentSpec.from_dict(config["spec"])
            emit({"t": "validation", **validate(spec), "spec_digest": spec.spec_digest, "resolved": spec.resolved()})
        except (TypeError, ValueError, KeyError) as error:
            emit({"t": "validation", "ok": False, "errors": [f"{type(error).__name__}: {error}"], "decisions": []})
        return 0

    cancel = threading.Event()

    def watch_stdin():
        for line in sys.stdin:
            try:
                if json.loads(line).get("cmd") == "cancel":
                    cancel.set()
            except ValueError:
                continue

    threading.Thread(target=watch_stdin, daemon=True).start()
    root = Path(config["experiments_root"])
    progress = lambda event: emit(_jsonable(event))  # noqa: E731
    try:
        if mode == "run":
            spec = ExperimentSpec.from_dict(config["spec"])
            out = engine.run(spec, root, progress=progress, cancel=cancel, experiment_id=config["experiment_id"])
            status = json.loads((out / "status.json").read_text())
            emit({"t": "closed", "state": status["state"], "experiment_id": out.name})
        elif mode == "replay":
            result = engine.replay(root / config["original_id"], root, progress=progress,
                                   experiment_id=config["experiment_id"])
            emit({"t": "replay", **result})
            emit({"t": "closed", "state": "completed", "experiment_id": result["replay"]})
        else:
            emit({"t": "error", "message": f"unknown mode {mode}"})
            return 2
    except Exception as error:  # reported to the server and the browser
        emit({"t": "error", "message": f"{type(error).__name__}: {error}"})
        emit({"t": "closed", "state": "failed"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
