from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()


class Settings:
    root_dir = Path(__file__).resolve().parent.parent
    app_host = os.getenv("APP_HOST", "0.0.0.0")
    app_port = int(os.getenv("APP_PORT", "8000"))
    max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "200"))
    storage_dir = Path(os.getenv("STORAGE_DIR", "storage"))
    gfix_bin = os.getenv("GFIX_BIN", "gfix")
    gbak_bin = os.getenv("GBAK_BIN", "gbak")
    firebird_user = os.getenv("FIREBIRD_USER", "SYSDBA")
    firebird_password = os.getenv("FIREBIRD_PASSWORD", "masterkey")

    @property
    def storage_path(self) -> Path:
        path = self.storage_dir
        if not path.is_absolute():
            path = self.root_dir / path
        return path

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
