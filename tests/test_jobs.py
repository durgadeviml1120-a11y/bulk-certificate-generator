import io
import zipfile

import pytest
from pypdf import PdfReader

from app.models import Job
from app.services import pdf, processor

GOOD = [{"name": "Asha Rao", "email": "asha@example.com"}, {"name": "Ben Carter"}, {"name": "Chen Wei"}]


def pdf_text(content: bytes) -> str:
    return PdfReader(io.BytesIO(content)).pages[0].extract_text()


# ---------- creating a job ----------

def test_create_job_returns_202_and_runs_in_background(client, make_job):
    r = make_job(GOOD)
    assert r.status_code == 202
    body = r.json()
    assert body["progress"]["total"] == 3
    assert body["links"]["certificates"] == f"/api/jobs/{body['id']}/certificates/"
    # the job finishes in the background; polling shows the final state
    final = client.get(f"/api/jobs/{body['id']}/").json()
    assert final["status"] == "COMPLETED"
    assert final["progress"] == {"total": 3, "pending": 0, "generated": 3, "failed": 0, "percent_complete": 100.0}


def test_issue_date_defaults_to_today(make_job):
    body = {"course_name": "X", "recipients": [{"name": "A"}]}
    r = make_job(body["recipients"], issue_date=None)
    assert r.status_code == 202 and r.json()["issue_date"]


def test_timestamps_are_explicit_utc(client, make_job):
    job = make_job(GOOD).json()
    final = client.get(f"/api/jobs/{job['id']}/").json()
    assert final["created_at"].endswith("Z") or final["created_at"].endswith("+00:00")
    assert final["finished_at"].endswith("Z") or final["finished_at"].endswith("+00:00")


# ---------- input validation ----------

@pytest.mark.parametrize(
    "overrides",
    [
        {"course_name": ""},
        {"course_name": "   "},
        {"course_name": "x" * 151},
        {"recipients": []},
        {"issue_date": "not-a-date"},
        {"recipients": "nope"},
    ],
)
def test_job_level_validation_returns_422(make_job, overrides):
    recipients = overrides.pop("recipients", GOOD)
    assert make_job(recipients, **overrides).status_code == 422


def test_missing_required_fields(client):
    assert client.post("/api/jobs/", json={}).status_code == 422
    assert client.post("/api/jobs/", json={"course_name": "X"}).status_code == 422


def test_too_many_recipients_rejected(make_job):
    assert make_job([{"name": f"P{i}"} for i in range(51)]).status_code == 422  # limit is 50 in tests


def test_invalid_recipients_are_recorded_not_fatal(client, make_job):
    r = make_job([
        {"name": "Good One"},
        {"name": ""},                                   # empty
        {"email": "a@b.com"},                           # missing name
        {"name": "Bad Email", "email": "not-an-email"},
        {"name": 123},                                  # wrong type
        "just a string",                                # not an object
        {"name": "Привет"},                             # characters the font can't draw
        {"name": "x" * 81},                             # too long
        {"name": "Last One", "email": "  l@x.org "},
    ])
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{r.json()['id']}/").json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["progress"]["generated"] == 2 and job["progress"]["failed"] == 7
    assert [f["index"] for f in job["failures"]] == [1, 2, 3, 4, 5, 6, 7]
    assert all(f["error"].startswith("Validation failed") for f in job["failures"])
    assert "name" in job["failures"][1]["error"]
    certs = client.get(f"/api/jobs/{job['id']}/certificates/?status=GENERATED").json()["items"]
    assert [c["recipient_name"] for c in certs] == ["Good One", "Last One"]
    assert certs[1]["recipient_email"] == "l@x.org"  # trimmed


def test_all_invalid_recipients_rejected_with_details(make_job):
    r = make_job([{"name": ""}, {}])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] == "No valid recipients in request"
    assert [e["index"] for e in detail["errors"]] == [0, 1]


def test_name_whitespace_is_normalised(client, make_job):
    job = make_job([{"name": "  Asha    Rao  "}]).json()
    item = client.get(f"/api/jobs/{job['id']}/certificates/").json()["items"][0]
    assert item["recipient_name"] == "Asha Rao"


# ---------- certificate generation ----------

def test_generated_certificate_is_a_pdf_with_recipient_data(client, make_job):
    job = make_job([{"name": "Asha Rao"}]).json()
    item = client.get(f"/api/jobs/{job['id']}/certificates/").json()["items"][0]
    r = client.get(item["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    text = pdf_text(r.content)
    assert "Asha Rao" in text and "Intro to Python" in text and "ACME ACADEMY" in text
    assert "7 October 2026" in text
    assert item["certificate_number"] in text


def test_render_certificate_directly():
    from datetime import date
    data = pdf.CertificateData("Zoë Müller", "Advanced Course", "", date(2026, 1, 2), "CERT-1")
    content = pdf.render_certificate(data)
    assert content.startswith(b"%PDF") and "Zoë Müller" in pdf_text(content)


def test_long_name_shrinks_to_fit_and_impossible_name_fails_cleanly():
    from datetime import date
    mk = lambda name: pdf.CertificateData(name, "C", "", date(2026, 1, 2), "N")  # noqa: E731
    assert pdf.render_certificate(mk("M" * 40)).startswith(b"%PDF")  # shrinks
    with pytest.raises(pdf.CertificateRenderError):
        pdf.render_certificate(mk("W" * 80))  # too wide even at the minimum size


# ---------- job status / progress ----------

def test_progress_is_visible_while_processing(client, make_job, monkeypatch):
    real_render, real_process = pdf.render_certificate, processor.process_job
    snapshots = []

    def spy(data):
        if data.recipient_name == "Third":  # what a client polling right now would see
            snapshots.append(client.get(f"/api/jobs/{job_id}/").json())
        return real_render(data)

    monkeypatch.setattr(pdf, "render_certificate", spy)
    # hold the job back so we can inspect it before processing starts
    monkeypatch.setattr(processor, "process_job", lambda jid: None)
    created = make_job([{"name": "First"}, {"name": "Second"}, {"name": "Third"}, {"name": "Fourth"}]).json()
    job_id = created["id"]
    assert created["status"] == "PENDING" and created["progress"]["percent_complete"] == 0.0
    assert client.get(f"/api/jobs/{job_id}/").json()["status"] == "PENDING"

    real_process(job_id)
    mid = snapshots[0]
    assert mid["status"] == "PROCESSING"
    assert mid["progress"]["generated"] == 2 and mid["progress"]["pending"] == 2
    assert mid["progress"]["percent_complete"] == 50.0
    assert client.get(f"/api/jobs/{job_id}/").json()["status"] == "COMPLETED"


def test_unknown_job_404(client):
    assert client.get("/api/jobs/doesnotexist/").status_code == 404
    assert client.get("/api/jobs/doesnotexist/certificates/").status_code == 404
    assert client.get("/api/jobs/doesnotexist/download/").status_code == 404


# ---------- individual failure handling ----------

def test_one_failing_certificate_does_not_block_others(client, make_job, monkeypatch):
    real = pdf.render_certificate

    def flaky(data):
        if data.recipient_name == "Boom":
            raise RuntimeError("renderer exploded")
        return real(data)

    monkeypatch.setattr(pdf, "render_certificate", flaky)
    job = make_job([{"name": "Ok One"}, {"name": "Boom"}, {"name": "Ok Two"}]).json()
    final = client.get(f"/api/jobs/{job['id']}/").json()
    assert final["status"] == "COMPLETED_WITH_ERRORS"
    assert final["progress"]["generated"] == 2 and final["progress"]["failed"] == 1
    assert final["failures"] == [{"index": 1, "recipient_name": "Boom", "error": "renderer exploded"}]
    items = client.get(f"/api/jobs/{job['id']}/certificates/").json()["items"]
    assert [i["status"] for i in items] == ["GENERATED", "FAILED", "GENERATED"]
    assert items[1]["download_url"] is None
    assert client.get(f"/api/certificates/{items[1]['id']}/").status_code == 409


def test_real_render_failure_is_isolated(client, make_job):
    job = make_job([{"name": "W" * 80}, {"name": "Fine"}]).json()  # first one cannot fit on the page
    final = client.get(f"/api/jobs/{job['id']}/").json()
    assert final["status"] == "COMPLETED_WITH_ERRORS"
    assert "too wide" in final["failures"][0]["error"]


def test_all_certificates_failing_marks_job_failed(client, make_job, monkeypatch):
    monkeypatch.setattr(pdf, "render_certificate", lambda d: (_ for _ in ()).throw(RuntimeError("nope")))
    job = make_job(GOOD).json()
    final = client.get(f"/api/jobs/{job['id']}/").json()
    assert final["status"] == "FAILED" and final["progress"]["failed"] == 3
    assert client.get(f"/api/jobs/{job['id']}/download/").status_code == 404


# ---------- retrieving certificates ----------

def test_list_filter_and_paginate_certificates(client, make_job):
    job = make_job([{"name": f"Person {i}"} for i in range(5)] + [{"name": ""}]).json()
    base = f"/api/jobs/{job['id']}/certificates/"
    page = client.get(base + "?limit=2&offset=2").json()
    assert page["total"] == 6 and [i["index"] for i in page["items"]] == [2, 3]
    assert client.get(base + "?status=FAILED").json()["total"] == 1
    assert client.get(base + "?status=BOGUS").status_code == 422


def test_download_zip_contains_only_generated_certificates(client, make_job):
    job = make_job([{"name": "Asha Rao"}, {"name": ""}, {"name": "Ben Carter"}]).json()
    r = client.get(f"/api/jobs/{job['id']}/download/")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist() == ["00001_Asha_Rao.pdf", "00003_Ben_Carter.pdf"]
    assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_download_zip_conflicts_while_job_not_finished(client, make_job, monkeypatch):
    monkeypatch.setattr(processor, "process_job", lambda job_id: None)
    job = make_job(GOOD).json()
    assert client.get(f"/api/jobs/{job['id']}/download/").status_code == 409


def test_unknown_certificate_404(client):
    assert client.get("/api/certificates/nope/").status_code == 404


# ---------- robustness ----------

def test_recover_jobs_resumes_interrupted_job(client, make_job, monkeypatch):
    real_process = processor.process_job
    monkeypatch.setattr(processor, "process_job", lambda job_id: None)
    job = make_job(GOOD).json()
    # simulate a crash mid-run: job stuck in PROCESSING
    from app.database import SessionLocal
    with SessionLocal() as db:
        db.get(Job, job["id"]).status = "PROCESSING"
        db.commit()
    assert job["id"] in processor.recover_jobs()
    real_process(job["id"])
    assert client.get(f"/api/jobs/{job['id']}/").json()["status"] == "COMPLETED"


def test_process_job_is_idempotent(client, make_job):
    job = make_job(GOOD).json()
    processor.process_job(job["id"])  # already COMPLETED -> no-op, no error
    assert client.get(f"/api/jobs/{job['id']}/").json()["progress"]["generated"] == 3
