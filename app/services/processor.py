"""Background processing of a certificate job."""
import logging

from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import (
    CERT_FAILED,
    CERT_GENERATED,
    CERT_PENDING,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    PENDING,
    PROCESSING,
    Certificate,
    Job,
    utcnow,
)
from app.services import pdf, storage

log = logging.getLogger(__name__)


def count_by_status(db, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    )
    return {status: n for status, n in rows}


def process_job(job_id: str) -> None:
    """Generate every PENDING certificate of a job.

    * Each certificate is isolated in its own try/except and its own commit, so one failure
      never blocks the others and progress is visible to status polls immediately.
    * Only PENDING certificates are touched, which makes the function safe to re-run after a
      crash (already generated certificates are skipped).
    """
    with SessionLocal() as db:
        # Atomically claim the job so two workers can never process it at the same time.
        claimed = db.execute(
            update(Job).where(Job.id == job_id, Job.status == PENDING).values(status=PROCESSING, started_at=utcnow())
        )
        db.commit()
        if claimed.rowcount == 0:
            return

        try:
            job = db.get(Job, job_id)
            cert_ids = db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CERT_PENDING)
                .order_by(Certificate.index)
            ).all()

            for cert_id in cert_ids:
                cert = db.get(Certificate, cert_id)
                try:
                    content = pdf.render_certificate(
                        pdf.CertificateData(
                            recipient_name=cert.recipient_name,
                            course_name=job.course_name,
                            organization=job.organization,
                            issue_date=job.issue_date,
                            certificate_number=cert.certificate_number,
                        )
                    )
                    rel = storage.relative_path(job_id, cert.id)
                    storage.save(rel, content)
                    cert.status, cert.file_path, cert.generated_at = CERT_GENERATED, rel, utcnow()
                except Exception as exc:  # isolate: record the failure, keep going
                    log.warning("certificate %s failed: %s", cert_id, exc)
                    cert.status, cert.error = CERT_FAILED, str(exc)[:500] or exc.__class__.__name__
                db.commit()

            counts = count_by_status(db, job_id)
            if counts.get(CERT_FAILED, 0) == 0:
                job.status = COMPLETED
            elif counts.get(CERT_GENERATED, 0) == 0:
                job.status = FAILED
            else:
                job.status = COMPLETED_WITH_ERRORS
            job.finished_at = utcnow()
            db.commit()
        except Exception as exc:  # unexpected (e.g. database/storage outage)
            log.exception("job %s crashed", job_id)
            db.rollback()
            db.execute(
                update(Job).where(Job.id == job_id).values(status=FAILED, error=str(exc)[:500], finished_at=utcnow())
            )
            db.commit()


def recover_jobs() -> list[str]:
    """Called on startup: jobs interrupted mid-run go back to PENDING and are resumed.

    Assumes a single application process (see README)."""
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.status == PROCESSING).values(status=PENDING))
        db.commit()
        return list(db.scalars(select(Job.id).where(Job.status == PENDING).order_by(Job.created_at)))
