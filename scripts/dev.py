"""One-command local setup and launch for Windows, macOS, and Linux."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    run(str(PYTHON), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt"))
    if not (ROOT / "frontend" / "node_modules").exists():
        run(shutil.which("npm") or "npm", "install", cwd=ROOT / "frontend")
    run(str(PYTHON), "-m", "alembic", "upgrade", "head", cwd=ROOT / "backend")
    api = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "faultline.api:app", "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT / "backend")
    web = subprocess.Popen([shutil.which("node") or "node", str(ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"), "--host", "127.0.0.1"], cwd=ROOT / "frontend")
    print("Faultline: http://127.0.0.1:5173", flush=True)
    try:
        web.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for process in (api, web):
            process.terminate()
        for process in (api, web):
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
