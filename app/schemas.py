from datetime import date, datetime
from typing import Annotated, Any

from datetime import timezone

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.validation import clean_text


def _as_utc(v: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; everything we store is UTC, so make that explicit in responses."""
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class JobCreate(BaseModel):
    """Job-level fields are validated strictly (422). Recipients are validated one by one
    later, so a single bad recipient does not reject the whole request."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "course_name": "Introduction to Python",
                "organization": "Acme Academy",
                "issue_date": "2026-10-07",
                "recipients": [
                    {"name": "Asha Rao", "email": "asha@example.com"},
                    {"name": "Ben Carter"},
                ],
            }
        }
    )

    course_name: str
    organization: str = ""
    issue_date: date | None = None
    recipients: list[Any] = Field(min_length=1)

    @field_validator("course_name")
    @classmethod
    def _course(cls, v: str) -> str:
        return clean_text(v, "course_name", 150)

    @field_validator("organization")
    @classmethod
    def _org(cls, v: str) -> str:
        return clean_text(v, "organization", 100, required=False)

    @field_validator("recipients")
    @classmethod
    def _limit(cls, v: list[Any]) -> list[Any]:
        if len(v) > settings.max_recipients_per_job:
            raise ValueError(f"at most {settings.max_recipients_per_job} recipients per job")
        return v


class Progress(BaseModel):
    total: int
    pending: int
    generated: int
    failed: int
    percent_complete: float


class FailureOut(BaseModel):
    index: int
    recipient_name: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    course_name: str
    organization: str
    issue_date: date
    status: str
    error: str | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    progress: Progress
    failures: list[FailureOut]
    failures_truncated: bool
    links: dict[str, str]


class CertificateOut(BaseModel):
    id: str
    index: int
    certificate_number: str
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    generated_at: UtcDateTime | None
    download_url: str | None


class CertificatePage(BaseModel):
    job_id: str
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
