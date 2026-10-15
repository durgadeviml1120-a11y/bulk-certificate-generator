import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import certificates, jobs
from app.config import settings
from app.database import Base, engine
from app.services.processor import process_job, recover_jobs


def _resume(job_ids: list[str]) -> None:
    for job_id in job_ids:
        process_job(job_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    pending = recover_jobs()  # resume anything interrupted by a restart
    if pending:
        threading.Thread(target=_resume, args=(pending,), daemon=True).start()
    yield


app = FastAPI(
    title="Bulk Certificate Generator API",
    description="Submit a list of recipients, track progress and download generated PDF certificates.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(jobs.router)
app.include_router(certificates.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
