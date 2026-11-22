"""HTTP server: REST endpoints, server-sent events and the static interface.

Standard library only. It binds to the loopback interface; requests whose Host
header is not a loopback name are refused (DNS-rebinding guard), and state-
changing requests need the header ``X-RNGLab-Lab: 1``, which a page from another
origin cannot send without a CORS preflight that this server never grants.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import subprocess
import tempfile
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import DATA_CLASSES, EVIDENCE_STATES, __version__, catalog
from .archive import Archive, read_json
from .codesources import GUI_ROOT, CodeSourceRegistry
from .guard import NamespaceRefused, check as check_namespace
from .runs import BOOTSTRAP, LaunchError, RunManager

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


class Lab:
    """Application state shared by request handlers."""

    def __init__(self, data_dir: Path, extra_sources: list[str] | None = None, detector: dict | None = None):
        self.archive = Archive(data_dir)
        self.sources = CodeSourceRegistry(catalog.requirements(), extra_sources)
        self.runs = RunManager(self.archive, self.sources)
        from .unified import Unified
        from .experiments import ExperimentManager
        self.unified = Unified(self, detector)
        self.experiments = ExperimentManager(data_dir, self.sources)
        self._study_cache: dict[str, tuple[float, dict]] = {}
        self._study_lock = threading.Lock()

    def study(self, source_id: str | None, query: dict, ttl: float = 600.0) -> dict:
        source = self.sources.get(source_id)
        key = json.dumps([source.id, source.head, query], sort_keys=True)
        with self._study_lock:
            cached = self._study_cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"code_source": {"path": source.path}, "query": dict(query)}, handle)
            path = handle.name
        try:
            done = subprocess.run([source.interpreter, "-I", str(BOOTSTRAP), path, "study"], capture_output=True,
                                  timeout=180)
        finally:
            Path(path).unlink(missing_ok=True)
        try:
            out = json.loads(done.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            out = {"ok": False, "error": done.stderr.decode(errors="replace")[-2000:] or "no output"}
        out["source"] = {"id": source.id, "path": source.path, "branch": source.branch, "head": source.head}
        if out.get("ok"):
            with self._study_lock:
                self._study_cache[key] = (time.monotonic(), out)
        return out


def make_handler(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"RNGLabLab/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: the interface shows what matters
            pass

        # -------------------------------------------------------------- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in LOOPBACK

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            body = json.dumps(data, allow_nan=False).encode()
            self._send(status, body, "application/json")

        def _error(self, status: int, message: str, **extra) -> None:
            self._json({"error": message, **extra}, status)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise LaunchError("bad Content-Length") from None
            if length < 0 or length > 1_000_000:
                raise LaunchError("bad or too large Content-Length")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise LaunchError("JSON object expected")
            return data

        # -------------------------------------------------------------- dispatch
        def do_GET(self):
            if not self._host_ok():
                return self._error(403, "host not allowed")
            url = urllib.parse.urlsplit(self.path)
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            parts = [p for p in url.path.split("/") if p]
            try:
                if not parts or parts[0] in ("index.html",):
                    return self._static("index.html")
                if parts[0] == "static":
                    return self._static("/".join(parts[1:]))
                if parts[0] != "api":
                    return self._error(404, "not found")
                return self._api_get(parts[1:], query)
            except KeyError as error:
                return self._error(404, str(error).strip("'\""))
            except (LaunchError, NamespaceRefused, ValueError) as error:  # ExperimentError is a ValueError
                return self._error(400, str(error), errors=getattr(error, "errors", {}))
            except BrokenPipeError:
                return None

        def do_POST(self):
            if not self._host_ok():
                return self._error(403, "host not allowed")
            if self.headers.get("X-RNGLab-Lab") != "1":
                return self._error(403, "missing X-RNGLab-Lab header")
            parts = [p for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
            try:
                body = self._body()
                return self._api_post(parts[1:] if parts and parts[0] == "api" else [], body)
            except KeyError as error:
                return self._error(404, str(error).strip("'\""))
            except (LaunchError, NamespaceRefused, ValueError) as error:  # ExperimentError is a ValueError
                return self._error(400, str(error), errors=getattr(error, "errors", {}))

        def _static(self, rel: str):
            path = (STATIC / rel).resolve()
            if STATIC not in path.parents or not path.is_file():
                return self._error(404, "not found")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                ctype = "text/javascript"
            self._send(200, path.read_bytes(), f"{ctype}; charset=utf-8" if ctype.startswith("text") else ctype)

        # -------------------------------------------------------------- GET API
        def _api_get(self, parts: list[str], query: dict):
            if parts == ["catalog"]:
                return self._json({"version": __version__, "models": [catalog.public(m) for m in catalog.MODELS],
                                   "generators": [list(g) for g in catalog.GENERATORS],
                                   "data_classes": DATA_CLASSES, "evidence_states": list(EVIDENCE_STATES)})
            if parts == ["sources"]:
                return self._json({"sources": [s.to_dict() for s in lab.sources.list(refresh=bool(query.get("refresh")))]})
            if parts == ["runs"]:
                active = {h.run_id: h.live() for h in lab.runs.active()}
                runs = lab.archive.list()
                for run in runs:
                    if run["run_id"] in active:
                        run["live"] = active[run["run_id"]]
                return self._json({"runs": runs})
            if parts == ["seeds"]:
                return self._json(seed_preview(query.get("namespace", ""), query.get("label", ""),
                                               query.get("roles", "local").split(","), query.get("source")))
            if parts and parts[0] == "studies":
                return self._study_get(parts[1:], query)
            if parts and parts[0] == "u":
                return self._json(lab.unified.get(parts[1:], query, [catalog.public(m) for m in catalog.MODELS]))
            if parts and parts[0] == "x":
                return self._experiment_get(parts[1:], query)
            if len(parts) >= 2 and parts[0] == "runs":
                run_id = parts[1]
                if len(parts) == 2:
                    detail = lab.archive.detail(run_id)
                    handle = lab.runs.get(run_id)
                    if handle is not None:
                        detail["live"] = handle.live()
                    return self._json(detail)
                if parts[2] == "telemetry":
                    return self._json({"messages": lab.archive.telemetry(run_id, int(query.get("after", 0)))})
                if parts[2] == "events":
                    return self._sse(run_id, int(query.get("after", 0)))
                if parts[2] == "arrays":
                    if len(parts) == 3:
                        return self._json({"arrays": lab.archive.array_names(run_id)})
                    row = int(query["row"]) if "row" in query else None
                    return self._json(lab.archive.array(run_id, parts[3], int(query.get("max", 5000)), row))
                if parts[2] == "export.zip":
                    data = lab.archive.export_zip(run_id)
                    return self._send(200, data, "application/zip",
                                      {"Content-Disposition": f'attachment; filename="{run_id}.zip"'})
            if parts == ["compare"]:
                return self._json(compare(lab, [i for i in query.get("ids", "").split(",") if i]))
            return self._error(404, "unknown endpoint")

        def _study_get(self, parts: list[str], query: dict):
            source = query.get("source")
            if parts == ["inventory"]:
                return self._json(lab.study(source, {"name": "inventory"}))
            if parts == ["h1", "cells"]:
                return self._json(lab.study(source, {"name": "h1_cells"}))
            if parts == ["h1", "runs"]:
                return self._json(lab.study(source, {"name": "h1_runs", "cell": query["cell"]}))
            if parts == ["h1", "run"]:
                return self._json(lab.study(source, {"name": "h1_run", "cell": query["cell"],
                                                     "replica": int(query["replica"])}))
            return self._error(404, "unknown study endpoint")

        # -------------------------------------------------------------- POST API
        def _api_post(self, parts: list[str], body: dict):
            if parts and parts[0] == "u":
                return self._json(lab.unified.post(parts[1:], body))
            if parts and parts[0] == "x":
                return self._experiment_post(parts[1:], body)
            if parts == ["runs"]:
                ids = lab.runs.launch(body.get("model", ""), body.get("params") or {}, body.get("source"),
                                      pair=bool(body.get("pair")))
                return self._json({"run_ids": ids}, 201)
            if len(parts) == 3 and parts[0] == "runs":
                run_id, action = parts[1], parts[2]
                if action == "control":
                    handle = lab.runs.get(run_id)
                    if handle is None:
                        raise LaunchError("run is not active")
                    cmd = body.get("cmd")
                    if cmd not in ("pause", "resume", "step", "speed", "stop"):
                        raise LaunchError("unknown command")
                    message = {"cmd": cmd}
                    if cmd == "step":
                        message["n"] = max(1, min(int(body.get("n", 1)), 10_000_000))
                    if cmd == "speed":
                        message["delay_ms"] = max(0.0, min(float(body.get("delay_ms", 0)), 60_000.0))
                    handle.control(message)
                    return self._json({"ok": True})
                if action == "restart":
                    return self._json({"run_ids": lab.runs.restart(run_id)}, 201)
                if action == "verify":
                    return self._json(lab.runs.verify(run_id), 202)
            return self._error(404, "unknown endpoint")

        # -------------------------------------------------------------- compound experiments
        def _experiment_get(self, parts: list[str], query: dict):
            X = lab.experiments
            if parts == ["catalog"]:
                return self._json(X.catalog(query.get("source")))
            if not parts:
                return self._json({"experiments": X.list()})
            if len(parts) == 1:
                return self._json(X.detail(parts[0]))
            if len(parts) == 2 and parts[1] == "events":
                return self._experiment_sse(parts[0], int(query.get("after", 0)))
            if len(parts) == 2 and parts[1] == "log":
                return self._json({"events": X.events_since(parts[0], int(query.get("after", 0)))})
            return self._error(404, "unknown experiment endpoint")

        def _experiment_post(self, parts: list[str], body: dict):
            X = lab.experiments
            spec = body.get("spec")
            if parts == ["validate"]:
                if not isinstance(spec, dict):
                    raise LaunchError("spec object expected")
                return self._json(X.validate(spec, body.get("source")))
            if parts == ["run"]:
                if not isinstance(spec, dict):
                    raise LaunchError("spec object expected")
                return self._json(X.run(spec, body.get("source")), 201)
            if len(parts) == 2 and parts[1] == "cancel":
                return self._json(X.cancel(parts[0]))
            if len(parts) == 2 and parts[1] == "replay":
                return self._json(X.replay(parts[0], body.get("source")), 201)
            return self._error(404, "unknown experiment endpoint")

        def _experiment_sse(self, exp_id: str, after: int):
            from .experiments import ID_RE
            if not ID_RE.match(exp_id):
                return self._error(404, "malformed experiment id")
            job = lab.experiments.job(exp_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = job.subscribe() if job is not None and not job.done.is_set() else None
            last = after
            try:
                for event in lab.experiments.events_since(exp_id, after):
                    last = max(last, event.get("seq", 0))
                    self._event(json.dumps(event))
                if q is None:
                    self._event(json.dumps({"t": "closed", "state": "archived"}))
                    return
                while True:
                    try:
                        line = q.get(timeout=10.0)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        if job.done.is_set():
                            break
                        continue
                    if line is None:
                        break
                    seq = json.loads(line).get("seq", 0)
                    if seq <= last:
                        continue
                    last = seq
                    self._event(line)
            except (BrokenPipeError, ConnectionResetError):
                pass

        # -------------------------------------------------------------- server-sent events
        def _sse(self, run_id: str, after: int):
            lab.archive.path(run_id)  # KeyError -> 404
            handle = lab.runs.get(run_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = handle.subscribe() if handle is not None and not handle.done.is_set() else None
            last = after
            try:
                for message in lab.archive.telemetry(run_id, after):
                    last = max(last, message.get("seq", 0))
                    self._event(json.dumps(message, allow_nan=False))
                if q is None:
                    self._event(json.dumps({"t": "closed", "state": "archived"}))
                    return
                idle = time.monotonic()
                while True:
                    try:
                        line = q.get(timeout=10.0)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        if handle.done.is_set() and time.monotonic() - idle > 10:
                            break
                        continue
                    idle = time.monotonic()
                    try:
                        seq = json.loads(line).get("seq", 0)
                    except ValueError:
                        seq = 0
                    if seq and seq <= last:
                        continue
                    last = max(last, seq)
                    self._event(line)
                    if '"t": "closed"' in line or '"t":"closed"' in line:
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if q is not None and handle is not None:
                    handle.unsubscribe(q)
            self.close_connection = True

        def _event(self, data: str) -> None:
            self.wfile.write(b"data: " + data.encode() + b"\n\n")
            self.wfile.flush()

    return Handler


def seed_preview(namespace: str, label: str, roles: list[str], source_id: str | None = None) -> dict:
    """Seeds by the documented rule (first 8 bytes of SHA-256 of 'namespace/label/role'), for display.

    The worker derives the seeds it uses with rnglab.seeds.namespace_seed and records them.
    """
    import hashlib
    try:
        check_namespace(namespace, label)
        ok, reason = True, None
    except NamespaceRefused as error:
        ok, reason = False, str(error)
    seeds = {}
    for role in roles:
        text = "/".join([namespace, label, role])
        seeds[role] = str(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big"))
    return {"allowed": ok, "reason": reason, "seeds": seeds,
            "rule": "int.from_bytes(sha256('namespace/label/role')[:8], 'big') (rnglab.seeds.namespace_seed)"}


def compare(lab: Lab, ids: list[str]) -> dict:
    runs = []
    for run_id in ids[:6]:
        detail = lab.archive.detail(run_id)
        runs.append({"run_id": run_id, "config": detail["config"], "status": detail["status"],
                     "summary": (detail["status"] or {}).get("summary"), "digest": (detail["status"] or {}).get("digest"),
                     "integrity": detail["integrity"], "verification": detail["verification"]})
    differing = {}
    if runs:
        keys = sorted({k for r in runs for k in (r["config"] or {}).get("params", {})})
        for key in keys:
            values = [(r["config"] or {}).get("params", {}).get(key) for r in runs]
            if any(v != values[0] for v in values):
                differing[key] = values
    same_model = len({(r["config"] or {}).get("model") for r in runs}) == 1
    digests = [r["digest"] for r in runs]
    return {"runs": runs, "differing_params": differing, "same_model": same_model,
            "identical_results": len(set(digests)) == 1 and None not in digests and len(digests) > 1}


def serve(host: str = "127.0.0.1", port: int = 8765, data_dir: str | Path | None = None,
          extra_sources: list[str] | None = None, detector: dict | None = None) -> ThreadingHTTPServer:
    data_dir = Path(data_dir) if data_dir else GUI_ROOT / "data"
    lab = Lab(data_dir, extra_sources, detector)
    server = ThreadingHTTPServer((host, port), make_handler(lab))
    server.daemon_threads = True
    server.lab = lab
    return server
