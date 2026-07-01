from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from backend.main import app
from backend.config import get_settings


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _open_browser(host: str, port: int) -> None:
    for _ in range(60):
        if _port_is_open(host, port):
            webbrowser.open(f"http://{host}:{port}")
            return
        time.sleep(0.25)


def main() -> None:
    os.environ.setdefault("APP_HOST", "127.0.0.1")
    os.environ.setdefault("APP_PORT", "8000")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        firebird_dir = exe_dir / "firebird"
        gfix = firebird_dir / "gfix.exe"
        gbak = firebird_dir / "gbak.exe"
        if gfix.exists():
            os.environ.setdefault("GFIX_BIN", str(gfix))
        if gbak.exists():
            os.environ.setdefault("GBAK_BIN", str(gbak))
    settings = get_settings()
    host = settings.app_host if settings.app_host not in {"0.0.0.0", "::"} else "127.0.0.1"
    threading.Thread(target=_open_browser, args=(host, settings.app_port), daemon=True).start()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port, log_level="info")


if __name__ == "__main__":
    main()
