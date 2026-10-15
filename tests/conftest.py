import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="cert_test_")
os.environ["CERT_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CERT_STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["CERT_MAX_RECIPIENTS_PER_JOB"] = "50"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_job(client):
    """Create a job and return the POST response. TestClient runs background tasks
    before returning, so the job is already finished when this returns."""

    def _make(recipients, **overrides):
        body = {"course_name": "Intro to Python", "organization": "Acme Academy",
                "issue_date": "2026-10-07", "recipients": recipients, **overrides}
        return client.post("/api/jobs/", json=body)

    return _make
