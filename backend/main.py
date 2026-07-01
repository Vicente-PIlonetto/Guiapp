from pathlib import Path
from uuid import uuid4
import asyncio
import json
import shutil
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.job_store import add_job, get_job
from backend.models import Job
from backend.module_registry import get_module, list_modules
from backend.runners import run_job
from backend.storage import ensure_storage, job_dirs, safe_display_name

app = FastAPI(title="GUINAPP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobStartRequest(BaseModel):
    module_id: str
    confirmation: bool = False
    upload_id: str


UPLOAD_TTL_SECONDS = 5 * 60


@app.on_event("startup")
def startup() -> None:
    ensure_storage()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def public_config() -> dict:
    settings = get_settings()
    return {
        "max_upload_mb": settings.max_upload_mb,
        "max_upload_gb": round(settings.max_upload_mb / 1024, 2),
        "upload_ttl_seconds": UPLOAD_TTL_SECONDS,
    }


@app.get("/api/modules")
def modules() -> dict:
    return {"modules": list_modules()}


def upload_dir(upload_id: str) -> Path:
    if not upload_id or any(char not in "0123456789abcdef" for char in upload_id):
        raise HTTPException(status_code=400, detail="Upload invalido.")
    return get_settings().storage_path / "uploads" / upload_id


async def cleanup_upload_later(upload_id: str) -> None:
    await asyncio.sleep(UPLOAD_TTL_SECONDS)
    shutil.rmtree(upload_dir(upload_id), ignore_errors=True)


def validate_upload(module_id: str, filename: str) -> tuple[str, str]:
    module = get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Modulo nao encontrado.")
    if not module.enabled:
        raise HTTPException(status_code=400, detail=module.disabled_reason or "Modulo indisponivel.")

    display_name = safe_display_name(filename or "upload.bin")
    suffix = Path(display_name).suffix.lower()
    if suffix not in module.accepted_extensions:
        raise HTTPException(status_code=400, detail=f"Extensao nao aceita: {suffix or '(sem extensao)'}")
    return display_name, suffix


def upload_limit_message() -> str:
    settings = get_settings()
    return f"Arquivo excede o tamanho maximo permitido ({settings.max_upload_mb} MB / {settings.max_upload_mb / 1024:.1f} GB)."


def create_upload_paths(module_id: str, filename: str) -> tuple[dict, Path, Path]:
    display_name, suffix = validate_upload(module_id, filename)
    upload_id = uuid4().hex
    target_dir = upload_dir(upload_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    upload_path = target_dir / f"original{suffix}"
    metadata = {
        "upload_id": upload_id,
        "module_id": module_id,
        "original_filename": display_name,
        "stored_filename": upload_path.name,
        "size": 0,
    }
    return metadata, target_dir, upload_path


async def save_upload(module_id: str, file: UploadFile) -> dict:
    metadata, target_dir, upload_path = create_upload_paths(module_id, file.filename or "upload.bin")
    max_bytes = get_settings().max_upload_bytes
    written = 0
    try:
        with upload_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=upload_limit_message())
                out.write(chunk)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    metadata["size"] = written
    (target_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


async def save_raw_upload(module_id: str, filename: str, request: Request) -> dict:
    metadata, target_dir, upload_path = create_upload_paths(module_id, filename)
    max_bytes = get_settings().max_upload_bytes
    written = 0
    try:
        with upload_path.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=upload_limit_message())
                out.write(chunk)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    if written == 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Arquivo vazio ou nao recebido.")

    metadata["size"] = written
    (target_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


@app.post("/api/uploads")
async def create_upload(
    background_tasks: BackgroundTasks,
    module_id: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    metadata = await save_upload(module_id, file)
    background_tasks.add_task(cleanup_upload_later, metadata["upload_id"])
    return {"upload": metadata}


@app.post("/api/uploads/raw")
async def create_raw_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    module_id: str,
    filename: str,
) -> dict:
    metadata = await save_raw_upload(module_id, filename, request)
    background_tasks.add_task(cleanup_upload_later, metadata["upload_id"])
    return {"upload": metadata}


def consume_upload(upload_id: str, module_id: str, job_id: str) -> tuple[str, Path]:
    source_dir = upload_dir(upload_id)
    metadata_path = source_dir / "metadata.json"
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Upload nao encontrado ou expirado.")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("module_id") != module_id:
        raise HTTPException(status_code=400, detail="Upload pertence a outro modulo.")

    source_path = source_dir / metadata["stored_filename"]
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo enviado nao encontrado.")

    paths = job_dirs(job_id)
    destination = paths["upload"] / metadata["stored_filename"]
    shutil.move(str(source_path), str(destination))
    shutil.rmtree(source_dir, ignore_errors=True)
    return metadata["original_filename"], destination


def create_job_from_upload(
    background_tasks: BackgroundTasks,
    module_id: str,
    confirmation: bool,
    upload_id: str,
) -> dict:
    module = get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Modulo nao encontrado.")
    if not module.enabled:
        raise HTTPException(status_code=400, detail=module.disabled_reason or "Modulo indisponivel.")
    if module.requires_confirmation and not confirmation:
        raise HTTPException(status_code=400, detail="Confirmacao explicita obrigatoria para este modulo.")

    job_id = uuid4().hex
    display_name, upload_path = consume_upload(upload_id, module.id, job_id)

    job = Job(id=job_id, module_id=module.id, original_filename=display_name)
    job.add_log("Upload recebido e validado.")
    add_job(job)
    background_tasks.add_task(run_job, job, module, upload_path)
    return {"job_id": job_id}


@app.post("/api/jobs/start")
async def start_job(
    payload: JobStartRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    return create_job_from_upload(
        background_tasks,
        payload.module_id,
        payload.confirmation,
        payload.upload_id,
    )


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    module_id: str = Form(...),
    confirmation: bool = Form(False),
    upload_id: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> dict:
    if upload_id:
        return create_job_from_upload(background_tasks, module_id, confirmation, upload_id)
    elif file:
        module = get_module(module_id)
        if not module:
            raise HTTPException(status_code=404, detail="Modulo nao encontrado.")
        if module.requires_confirmation and not confirmation:
            raise HTTPException(status_code=400, detail="Confirmacao explicita obrigatoria para este modulo.")
        job_id = uuid4().hex
        metadata = await save_upload(module.id, file)
        display_name, upload_path = consume_upload(metadata["upload_id"], module.id, job_id)
    else:
        raise HTTPException(status_code=400, detail="Envie um arquivo ou informe um upload_id.")

    job = Job(id=job_id, module_id=module.id, original_filename=display_name)
    job.add_log("Upload recebido e validado.")
    add_job(job)
    background_tasks.add_task(run_job, job, module, upload_path)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")
    return {"job": job.public_dict()}


@app.get("/api/jobs/{job_id}/report")
def download_report(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if not job or not job.report_path or not job.report_path.exists():
        raise HTTPException(status_code=404, detail="Relatorio nao encontrado.")
    return FileResponse(job.report_path, filename=job.report_path.name)


@app.get("/api/jobs/{job_id}/output")
def download_output(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo de saida nao encontrado.")
    return FileResponse(job.output_path, filename=job.output_path.name)


def frontend_dist_path() -> Path | None:
    settings = get_settings()
    candidates = [
        settings.asset_dir / "frontend" / "dist",
        settings.root_dir / "_internal" / "frontend" / "dist",
        settings.root_dir / "frontend" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


@app.get("/")
def frontend_index() -> FileResponse:
    dist = frontend_dist_path()
    if not dist:
        raise HTTPException(status_code=404, detail="Frontend build nao encontrado.")
    return FileResponse(dist / "index.html")


@app.get("/{path:path}")
def frontend_asset_or_index(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    dist = frontend_dist_path()
    if not dist:
        raise HTTPException(status_code=404, detail="Frontend build nao encontrado.")
    requested = (dist / path).resolve()
    if dist.resolve() in requested.parents and requested.is_file():
        return FileResponse(requested)
    return FileResponse(dist / "index.html")
