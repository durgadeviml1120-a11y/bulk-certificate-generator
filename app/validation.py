"""Input validation shared by the job-level and recipient-level checks."""
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

NAME_MAX = 80
EMAIL_MAX = 254
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_text(value: str, field: str, max_len: int, required: bool = True) -> str:
    """Normalise whitespace and reject empty / control / non-renderable text.

    The certificate uses a built-in PDF font (WinAnsi encoding), so characters outside
    Windows-1252 cannot be drawn. Rejecting them up front gives a clear error instead of
    a certificate with missing glyphs.
    """
    value = " ".join(value.split())  # trim + collapse whitespace
    if not value:
        if required:
            raise ValueError(f"{field} must not be empty")
        return value
    if len(value) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError(f"{field} contains control characters")
    try:
        value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"{field} contains characters the certificate font cannot render ({exc.object[exc.start]!r})"
        ) from None
    return value


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    email: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_text(v, "name", NAME_MAX)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > EMAIL_MAX or not _EMAIL_RE.match(v):
            raise ValueError("email is not a valid address")
        return v


def validate_recipient(raw: Any) -> tuple[RecipientIn | None, str | None]:
    """Validate one recipient. Returns (recipient, None) or (None, human-readable error)."""
    try:
        return RecipientIn.model_validate(raw), None
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "recipient"
            msg = err["msg"].removeprefix("Value error, ")
            parts.append(msg if msg.startswith(loc) else f"{loc}: {msg}")
        return None, "; ".join(parts)
