"""Start the laboratory: ``python -m rnglab_gui`` from the ``gui`` directory, or ``gui/rnglab-lab``."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rnglab-lab", description="RNGLab Laboratory: observe RNGLab simulations")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", help="loopback only by design; other hosts are refused")
    parser.add_argument("--data-dir", default=None, help="where runs are stored (default: gui/data)")
    parser.add_argument("--source", action="append", default=[], help="extra checkout to offer as a code source")
    parser.add_argument("--open", action="store_true", help="open the interface in the default browser")
    parser.add_argument("--detector-file", default=None,
                        help="recorded detector waveform export to audit read-only (explicit consent: only this file)")
    parser.add_argument("--detector-samples", type=int, default=None, help="samples per waveform record in that file")
    parser.add_argument("--detector-provenance", default=None, help="free-text provenance of that file (required with it)")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("refusing to bind a non-loopback address", file=sys.stderr)
        return 2
    detector = None
    if args.detector_file:
        if not args.detector_samples or not args.detector_provenance:
            print("--detector-file needs --detector-samples and --detector-provenance", file=sys.stderr)
            return 2
        detector = {"path": str(Path(args.detector_file).expanduser().resolve()), "samples": args.detector_samples,
                    "provenance": args.detector_provenance}
    server = serve(args.host, args.port, args.data_dir, args.source, detector)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"RNGLab Laboratory at {url}  (runs stored in {server.lab.archive.root})", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for handle in server.lab.runs.active():
            try:
                handle.control({"cmd": "stop"})
            except Exception:
                pass
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
