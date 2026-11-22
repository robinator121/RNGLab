"""One module per catalog model id ('-' becomes '_'). Each exposes ``run(ctx) -> outcome``.

An adapter composes the sources through ``ctx.source_for`` (identical in the
observed and the direct mode), makes the official scientific call unchanged,
and, when observed, reads state at step boundaries. It returns

    {"payload": <official result and run constants>, "checks": [...], "summary": {...}}

``payload`` is what the reproducibility digest covers.
"""
