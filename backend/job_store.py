from threading import Lock
from backend.models import Job

_jobs: dict[str, Job] = {}
_lock = Lock()


def add_job(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update_job(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job
