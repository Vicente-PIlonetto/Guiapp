from pathlib import Path
import re
import shutil

from backend.config import get_settings

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def ensure_storage() -> None:
    base = get_settings().storage_path
    for name in ("uploads", "processing", "backups", "results", "logs"):
        (base / name).mkdir(parents=True, exist_ok=True)


def safe_display_name(filename: str) -> str:
    name = Path(filename or "upload.bin").name
    cleaned = SAFE_NAME_RE.sub("_", name).strip("._")
    return cleaned or "upload.bin"


def job_dirs(job_id: str) -> dict[str, Path]:
    base = get_settings().storage_path
    paths = {
        "upload": base / "uploads" / job_id,
        "processing": base / "processing" / job_id,
        "backup": base / "backups" / job_id,
        "result": base / "results" / job_id,
        "log": base / "logs" / f"{job_id}.log",
    }
    for key in ("upload", "processing", "backup", "result"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["log"].parent.mkdir(parents=True, exist_ok=True)
    return paths


def copy_to(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
