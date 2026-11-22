"""π by hit-or-miss: rnglab14.models.pi_mc.run_pi_monte_carlo, observed at the random-number boundary.

Each sample reads two 8-byte words, x then y. The scatter shows those uniforms,
converted exactly as RandomSource.random_float converts them. The running count
shown is the function's own ``inside`` variable; the laboratory's
reconstruction from the uniforms is compared with it at every step boundary and
with the official result at the end.
"""
from __future__ import annotations

import math
import struct

from ..runtime import FrameProbe, check

SCALE = 1.0 / 9007199254740992.0  # the constant RandomSource.random_float uses


def pi_interval(inside: int, n: int) -> dict:
    """The function's own estimate and interval formula at a prefix of n samples."""
    p_hat = inside / n
    pi_hat = 4.0 * p_hat
    var_pi = 16.0 * p_hat * (1.0 - p_hat) / n if n > 1 else 1.0
    se = math.sqrt(var_pi)
    return {"x": n, "mean": pi_hat, "lo": pi_hat - 1.95996 * se, "hi": pi_hat + 1.95996 * se, "se": se}


class PiObserver:
    def __init__(self, ctx, samples: int, code):
        self.ctx = ctx
        self.samples = samples
        self.probe = FrameProbe(code)
        self.reads = 0
        self.x: float | None = None
        self.recon_inside = 0
        self.completed = 0
        self.mismatch: dict | None = None
        self.bad_read: int | None = None
        self.points: list = []
        self.keep = ctx.params["points_kept"]
        self.ci_points: list = []
        self.next_checkpoint = 1.0
        self.last_stream = -1e9

    def on_read(self, tap, raw: bytes) -> None:
        if len(raw) != 8:
            self.bad_read = self.bad_read or len(raw)
            return
        u = (struct.unpack(">Q", raw)[0] >> 11) * SCALE
        if self.reads % 2 == 0:
            if self.reads:  # a new sample starts: the previous one is complete, including the model's update
                self.step_done()
            self.x = u
        else:
            inside = self.x * self.x + u * u <= 1.0
            self.recon_inside += inside
            self.points.append((self.x, u, 1 if inside else 0))
            if len(self.points) > self.keep:
                del self.points[: len(self.points) - self.keep]
        self.reads += 1

    def step_done(self) -> None:
        self.completed += 1
        n = self.completed
        if self.probe.attach():
            model_inside = self.probe.get("inside")
            if model_inside is not None and model_inside != self.recon_inside and self.mismatch is None:
                self.mismatch = {"sample": n, "model_inside": model_inside, "reconstructed": self.recon_inside}
        if n >= self.next_checkpoint:
            self.ci_points.append(pi_interval(self.recon_inside, n))
            while self.next_checkpoint <= n:
                self.next_checkpoint = max(self.next_checkpoint * 1.06, self.next_checkpoint + 1)
        if self.ctx.due():
            self.emit()
        self.ctx.boundary(n, self.emit)

    def emit(self, final_result=None) -> None:
        n = self.completed
        data: dict = {}
        if n:
            last = pi_interval(self.recon_inside, n)
            if not self.ci_points or self.ci_points[-1]["x"] != n:
                self.ci_points.append(last)
            data["series"] = {
                "ci": self.ci_points,
                "abs_err": {"x": [p["x"] for p in self.ci_points], "y": [abs(p["mean"] - math.pi) for p in self.ci_points]},
                "se": {"x": [p["x"] for p in self.ci_points], "y": [p["se"] for p in self.ci_points]},
            }
            data["scalars"] = {"samples": n, "inside": self.recon_inside, "estimate": last["mean"],
                               "se": last["se"], "z": (last["mean"] - math.pi) / last["se"] if last["se"] else None}
        data["append"] = {"points": self.points}
        self.points = []
        self.ci_points = []
        import time
        now = time.monotonic()
        if now - self.last_stream >= 1.0 or final_result is not None:
            data["streams"] = self.ctx.stream_data()
            self.last_stream = now
        self.ctx.frame({"unit": "sample", "value": n, "total": self.samples}, data,
                       progress=n / self.samples if self.samples else None)


def run(ctx) -> dict:
    from rnglab14.models.pi_mc import run_pi_monte_carlo

    n = ctx.params["samples"]
    observer = PiObserver(ctx, n, run_pi_monte_carlo.__code__) if ctx.observed else None
    source = ctx.source_for("local", faulted=True, on_read=observer.on_read if observer else None)
    ctx.meta(unit="sample", total=n, references={"pi": math.pi},
             roles=[t.role for t in ctx.taps], call="rnglab14.models.pi_mc.run_pi_monte_carlo(source, samples)")
    result = run_pi_monte_carlo(source, samples=n)
    checks = []
    if observer is not None:
        observer.completed = n
        observer.probe.detach()
        observer.emit(final_result=result)
        recon_ok = observer.recon_inside == result.inside_circle and observer.bad_read is None
        checks.append(check("reconstruction vs official count",
                            "ok" if recon_ok else "failed",
                            "points reconstructed from the tapped words give the function's own inside count"
                            if recon_ok else "the reconstruction disagrees with the official count",
                            reconstructed=observer.recon_inside, official=result.inside_circle))
        checks.append(check("live count vs running function",
                            "ok" if observer.mismatch is None else "failed",
                            "the reconstruction equalled the function's 'inside' variable at every step boundary"
                            if observer.mismatch is None else "mismatch during the run",
                            **({"first_mismatch": observer.mismatch} if observer.mismatch else {})))
    summary = {"estimate": result.estimated_pi, "interval_95": list(result.confidence_interval_95),
               "covers_pi": result.confidence_interval_95[0] <= math.pi <= result.confidence_interval_95[1],
               "z": result.z_score}
    payload = {"call": "rnglab14.models.pi_mc.run_pi_monte_carlo", "samples": n, "result": result}
    return {"payload": payload, "checks": checks, "summary": summary}
