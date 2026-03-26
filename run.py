#!/usr/bin/env python3
"""
Start the HubSpot ↔ Xero bridge web UI.

From this folder run:  python3 run.py
If ./.venv exists, this file re-executes with .venv/bin/python so dependencies (uvicorn, etc.) load.

Then open:            http://127.0.0.1:8080
(Port 8080 — not 5000/5001 unless you set PORT.)
If you see "Address already in use", another process is using 8080 (often an old bridge). Use
  PORT=8081 python3 run.py
or stop the other process, e.g.  lsof -i :8080
"""
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
_venv_root = _root / ".venv"
_venv_python = None
for _name in ("python3", "python"):
    _candidate = _venv_root / "bin" / _name
    if _candidate.is_file():
        _venv_python = _candidate
        break

# Re-exec with the venv interpreter when needed. Do not compare sys.executable to
# .venv/bin/python — on macOS they often resolve to the same real binary, but only
# the venv entrypoint loads .venv/lib/.../site-packages (uvicorn, etc.).
if _venv_python is not None:
    try:
        if Path(sys.prefix).resolve() != _venv_root.resolve():
            os.execv(
                str(_venv_python),
                [str(_venv_python), str(_root / "run.py"), *sys.argv[1:]],
            )
    except OSError as exc:
        print(
            f"Could not start {_venv_python}: {exc}\n"
            "Try: .venv/bin/pip install -r requirements.txt\n"
            "Then: .venv/bin/python run.py",
            file=sys.stderr,
        )
        sys.exit(1)

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    # Local dev: 127.0.0.1 + reload. For a public bind (e.g. VM), set HOST=0.0.0.0 and UVICORN_RELOAD=0.
    host = os.environ.get("HOST", "127.0.0.1")
    reload = os.environ.get("UVICORN_RELOAD", "1").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
    )
