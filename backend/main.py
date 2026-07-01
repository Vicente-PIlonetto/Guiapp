from pathlib import Path
from uuid import uuid4
import shutil

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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


@app.on_event("startup")
def startup() -> None:
    ensure_storage()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/modules")
def modules() -> dict:
    return {"modules": list_modules()}


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    module_id: str = Form(...),
    confirmation: bool = Form(False),
    file: UploadFile = File(...),
) -> dict:
    module = get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Modulo nao encontrado.")
    if not module.enabled:
        raise HTTPException(status_code=400, detail=module.disabled_reason or "Modulo indisponivel.")
    if module.requires_confirmation and not confirmation:
        raise HTTPException(status_code=400, detail="Confirmacao explicita obrigatoria para este modulo.")

    display_name = safe_display_name(file.filename or "upload.bin")
    suffix = Path(display_name).suffix.lower()
    if suffix not in module.accepted_extensions:
        raise HTTPException(status_code=400, detail=f"Extensao nao aceita: {suffix or '(sem extensao)'}")

    job_id = uuid4().hex
    paths = job_dirs(job_id)
    upload_path = paths["upload"] / f"original{suffix}"

    max_bytes = get_settings().max_upload_bytes
    written = 0
    with upload_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                shutil.rmtree(paths["upload"], ignore_errors=True)
                raise HTTPException(status_code=413, detail="Arquivo excede o tamanho maximo permitido.")
            out.write(chunk)

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
