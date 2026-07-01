from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from backend.main import app
from backend.config import get_settings


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
    display_host = settings.app_host if settings.app_host not in {"0.0.0.0", "::"} else "127.0.0.1"
    print(f"GUINAPP rodando em http://{display_host}:{settings.app_port}")
    uvicorn.run(app, host=settings.app_host, port=settings.app_port, log_level="info")


if __name__ == "__main__":
    main()
