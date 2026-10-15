"""The single predefined certificate template, drawn with reportlab (A4 landscape)."""
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

NAVY = HexColor("#1f3a5f")
GOLD = HexColor("#b08d57")
GREY = HexColor("#555555")

PAGE_W, PAGE_H = landscape(A4)  # 842 x 595 pt
CONTENT_W = PAGE_W - 160


class CertificateRenderError(Exception):
    """The certificate could not be drawn (e.g. text does not fit)."""


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    course_name: str
    organization: str
    issue_date: date
    certificate_number: str


def _fit_font_size(text: str, font: str, max_size: float, min_size: float, width: float) -> float:
    """Largest font size (<= max_size) at which text fits in width; error if even min_size is too big."""
    size = max_size
    while size >= min_size:
        if stringWidth(text, font, size) <= width:
            return size
        size -= 1
    raise CertificateRenderError(f"Text is too wide to fit on the certificate: {text[:30]!r}...")


def _centered(c: canvas.Canvas, text: str, y: float, font: str, size: float, color=NAVY, char_space=0):
    c.setFont(font, size)
    c.setFillColor(color)
    if char_space:
        text_w = stringWidth(text, font, size) + char_space * (len(text) - 1)
        t = c.beginText((PAGE_W - text_w) / 2, y)
        t.setFont(font, size)
        t.setCharSpace(char_space)
        t.textOut(text)
        t.setCharSpace(0)  # character spacing is part of the PDF graphics state: reset it
        c.drawText(t)
    else:
        c.drawCentredString(PAGE_W / 2, y, text)


def render_certificate(data: CertificateData) -> bytes:
    """Render one certificate and return the PDF bytes."""
    name_size = _fit_font_size(data.recipient_name, "Helvetica-Bold", 40, 14, CONTENT_W)
    course_lines = simpleSplit(data.course_name, "Helvetica-Bold", 24, CONTENT_W)
    if len(course_lines) > 3:
        raise CertificateRenderError("Course name is too long to fit on the certificate")

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {data.recipient_name}")
    c.setAuthor(data.organization or "Certificate Generator")
    c.setSubject(data.course_name)

    # Borders
    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.rect(34, 34, PAGE_W - 68, PAGE_H - 68)

    if data.organization:
        _centered(c, data.organization.upper(), PAGE_H - 85, "Helvetica-Bold", 14, GREY, char_space=2)

    _centered(c, "CERTIFICATE", PAGE_H - 140, "Helvetica-Bold", 46)
    _centered(c, "OF COMPLETION", PAGE_H - 168, "Helvetica", 16, GOLD, char_space=4)

    _centered(c, "This certificate is proudly presented to", PAGE_H - 215, "Helvetica-Oblique", 14, GREY)
    _centered(c, data.recipient_name, PAGE_H - 270, "Helvetica-Bold", name_size)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(PAGE_W / 2 - 220, PAGE_H - 282, PAGE_W / 2 + 220, PAGE_H - 282)

    _centered(c, "for successfully completing", PAGE_H - 318, "Helvetica-Oblique", 14, GREY)
    y = PAGE_H - 355
    for line in course_lines:
        _centered(c, line, y, "Helvetica-Bold", 24)
        y -= 30

    # Footer: date (left), signature (right), certificate number (centre)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.8)
    c.line(110, 105, 310, 105)
    c.line(PAGE_W - 310, 105, PAGE_W - 110, 105)
    c.setFillColor(NAVY)
    c.setFont("Helvetica", 13)
    c.drawCentredString(210, 112, f"{data.issue_date.day} {data.issue_date:%B %Y}")
    c.setFillColor(GREY)
    c.setFont("Helvetica", 10)
    c.drawCentredString(210, 90, "DATE OF ISSUE")
    c.drawCentredString(PAGE_W - 210, 90, "AUTHORIZED SIGNATURE")
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, 52, f"Certificate No. {data.certificate_number}")

    c.showPage()
    c.save()
    return buf.getvalue()
