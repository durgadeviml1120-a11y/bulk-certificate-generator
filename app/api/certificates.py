from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CERT_GENERATED, Certificate
from app.services import storage

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


@router.get("/{certificate_id}/")
def get_certificate(certificate_id: str, db: Session = Depends(get_db)):
    """Download one generated certificate as a PDF."""
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CERT_GENERATED:
        raise HTTPException(409, f"Certificate is {cert.status}; no file available")
    path = storage.absolute_path(cert.file_path)
    if not path.exists():
        raise HTTPException(410, "Certificate file is no longer available")
    return FileResponse(
        path, media_type="application/pdf", filename=f"{cert.certificate_number}.pdf",
        content_disposition_type="inline",
    )
