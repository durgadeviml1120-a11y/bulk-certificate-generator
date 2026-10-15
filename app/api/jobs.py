import os
import re
import tempfile
import zipfile
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.database import get_db
from app.models import CERT_FAILED, CERT_GENERATED, JOB_TERMINAL, Certificate, Job, new_id
from app.schemas import CertificateOut, CertificatePage, FailureOut, JobCreate, JobOut, Progress
from app.services import processor, storage
from app.validation import validate_recipient

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MAX_FAILURES_IN_STATUS = 100
MAX_ERRORS_IN_422 = 100


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def build_job_out(db: Session, job: Job) -> JobOut:
    counts = processor.count_by_status(db, job.id)
    generated, failed = counts.get(CERT_GENERATED, 0), counts.get(CERT_FAILED, 0)
    total = job.total
    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CERT_FAILED)
        .order_by(Certificate.index)
        .limit(MAX_FAILURES_IN_STATUS)
    ).all()
    base = f"/api/jobs/{job.id}"
    return JobOut(
        id=job.id,
        course_name=job.course_name,
        organization=job.organization,
        issue_date=job.issue_date,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=Progress(
            total=total,
            pending=total - generated - failed,
            generated=generated,
            failed=failed,
            percent_complete=round((generated + failed) / total * 100, 1) if total else 100.0,
        ),
        failures=[FailureOut(index=c.index, recipient_name=c.recipient_name, error=c.error) for c in failures],
        failures_truncated=failed > MAX_FAILURES_IN_STATUS,
        links={"self": f"{base}/", "certificates": f"{base}/certificates/", "download_zip": f"{base}/download/"},
    )


def _cert_out(c: Certificate) -> CertificateOut:
    return CertificateOut(
        id=c.id, index=c.index, certificate_number=c.certificate_number,
        recipient_name=c.recipient_name, recipient_email=c.recipient_email,
        status=c.status, error=c.error, generated_at=c.generated_at,
        download_url=f"/api/certificates/{c.id}/" if c.status == CERT_GENERATED else None,
    )


@router.post("/", response_model=JobOut, status_code=202)
def create_job(payload: JobCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Submit a bulk request. Returns immediately (202); certificates are generated in the background.

    Each recipient is validated individually: invalid ones are recorded as FAILED with the reason,
    valid ones are queued. If *no* recipient is valid the request is rejected with 422."""
    job_id = new_id()
    short = job_id[:8].upper()
    certs, errors = [], []

    for i, raw in enumerate(payload.recipients):
        recipient, error = validate_recipient(raw)
        raw_name = raw.get("name") if isinstance(raw, dict) else None
        raw_email = raw.get("email") if isinstance(raw, dict) else None
        certs.append(
            Certificate(
                job_id=job_id,
                index=i,
                certificate_number=f"CERT-{short}-{i + 1:05d}",
                recipient_name=recipient.name if recipient else (str(raw_name)[:255] if raw_name is not None else None),
                recipient_email=recipient.email if recipient else (str(raw_email)[:255] if raw_email is not None else None),
                status="PENDING" if recipient else CERT_FAILED,
                error=None if recipient else f"Validation failed: {error}",
            )
        )
        if error:
            errors.append({"index": i, "error": error})

    if len(errors) == len(certs):
        raise HTTPException(
            422,
            detail={"message": "No valid recipients in request", "errors": errors[:MAX_ERRORS_IN_422]},
        )

    job = Job(
        id=job_id,
        course_name=payload.course_name,
        organization=payload.organization,
        issue_date=payload.issue_date or date.today(),
        status="PENDING",
        total=len(certs),
    )
    db.add(job)
    db.add_all(certs)
    db.commit()

    background_tasks.add_task(processor.process_job, job_id)
    return build_job_out(db, job)


@router.get("/{job_id}/", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Job status and progress, including which certificates failed and why."""
    return build_job_out(db, _get_job_or_404(db, job_id))


@router.get("/{job_id}/certificates/", response_model=CertificatePage)
def list_certificates(
    job_id: str,
    status: str | None = Query(None, pattern="^(PENDING|GENERATED|FAILED)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        query = query.where(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Certificate.index).limit(limit).offset(offset)).all()
    return CertificatePage(job_id=job_id, total=total, limit=limit, offset=offset, items=[_cert_out(c) for c in rows])


def _slug(text: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:50] or "certificate"


@router.get("/{job_id}/download/")
def download_zip(job_id: str, db: Session = Depends(get_db)):
    """All generated certificates of a finished job as one zip archive."""
    job = _get_job_or_404(db, job_id)
    if job.status not in JOB_TERMINAL:
        raise HTTPException(409, f"Job is {job.status}; download is available once it has finished")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CERT_GENERATED)
        .order_by(Certificate.index)
    ).all()
    if not certs:
        raise HTTPException(404, "No certificates were generated for this job")

    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in certs:
            zf.write(storage.absolute_path(c.file_path), f"{c.index + 1:05d}_{_slug(c.recipient_name)}.pdf")
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"certificates_{job_id[:8]}.zip",
        background=BackgroundTask(os.unlink, tmp_path),  # remove the temp file after sending
    )
