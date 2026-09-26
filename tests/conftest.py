"""Route all test configuration to temporary storage, including CLI tests."""

import pytest

from app.database import Database


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSIGNMENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ASSIGNMENT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GMAIL_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    for name in ("ASSIGNMENT_NOTIFICATION_DESTINATION", "ALERT_EMAIL", "GMAIL_KEYWORDS", "GMAIL_SCAN_DAYS", "GMAIL_MAX_MESSAGES", "GMAIL_UNIVERSITY_DOMAIN",
                 "GMAIL_LMS_SENDER", "GMAIL_TEACHER_EMAILS", "GMAIL_SENDER_PATTERNS",
                 "ASSIGNMENT_SCORE_THRESHOLD", "TRUSTED_ACADEMIC_SENDERS", "TRUSTED_ACADEMIC_DOMAINS",
                 "GMAIL_FETCH_DELAY_MS", "GMAIL_MAX_RETRIES", "GMAIL_SEARCH_TERMS", "GMAIL_UNREAD_ONLY",
                 "GMAIL_DISCOVERY_TERMS", "GMAIL_FALLBACK_MESSAGES", "ASSIGNMENT_LMS_BACKGROUND", "LMS_COURSE_INCLUDE", "LMS_COURSE_EXCLUDE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / "data" / "assignments.db")


@pytest.fixture(autouse=True)
def block_real_notification_processes(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must mock notification subprocess calls")
    monkeypatch.setattr("app.notifier.subprocess.run", blocked)


@pytest.fixture(autouse=True)
def block_network_and_browser(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must mock network and OAuth browser access")
    monkeypatch.setattr("socket.socket.connect", blocked)
    monkeypatch.setattr("socket.create_connection", blocked)
    monkeypatch.setattr("webbrowser.open", blocked)
    monkeypatch.setattr("webbrowser.get", blocked)


@pytest.fixture
def gmail_message():
    import base64
    from datetime import datetime, timezone

    def make(*, message_id="synthetic-message-1", subject="[Artificial Intelligence] Assignment 3",
             sender="Test Teacher <teacher@example.edu>", mime="text/plain", body=None):
        if body is None:
            body = ("Course: Artificial Intelligence\nAssignment: Assignment 3\nTeacher: Test Teacher\n"
                    "Deadline: September 20, 2026 at 11:59 PM\n"
                    "Prepare your own report. https://lms.example.edu/assignment/3")
        return {"id": message_id, "internalDate": str(int(datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)),
                "payload": {"mimeType": mime,
                            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}],
                            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")}}}
    return make


@pytest.fixture(autouse=True)
def mock_gmail_waits(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr("app.gmail_retry.time.sleep", Mock())
