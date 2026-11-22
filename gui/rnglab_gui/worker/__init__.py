"""Worker side of the laboratory: runs in a child process, one per run.

``bootstrap.py`` is started with ``python -I`` and puts exactly two entries in
front of ``sys.path``: the laboratory root (package ``rnglab_gui`` only) and the
chosen code source's ``src``. Scientific modules are therefore imported from one
checkout only, and never into the server process.
"""
