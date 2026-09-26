import base64
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.assignment_parser import (MalformedEmailError, assignment_from_email, extract_deadline,
                                   is_assignment_email, parse_email, sender_allowed)
from app.config import GmailSettings, load_gmail_settings
from app.models import Source


@pytest.mark.parametrize("keyword,accepted", [
    ("assignment", False), ("new assignment", True), ("assignment uploaded", True),
    ("assignment posted", True), ("deadline", False), ("due date", False),
    ("submission", False), ("homework", False), ("coursework", False),
    ("quiz", False), ("project", False), ("LMS", False),
])
def test_keywords_require_strong_enough_evidence(gmail_message, tmp_path, keyword, accepted):
    settings = GmailSettings(tmp_path)
    assert is_assignment_email(parse_email(gmail_message(subject=keyword, body="")), settings) is accepted


def test_custom_keywords_are_candidate_hints_only(gmail_message, monkeypatch):
    from app.assignment_classifier import classify_email
    monkeypatch.setenv("GMAIL_KEYWORDS", "assessment,lab work")
    settings = load_gmail_settings()
    result = classify_email(parse_email(gmail_message(subject="Lab work", body="")), settings)
    assert result.likely_candidate and result.classification == "REJECTED"
    assert is_assignment_email(parse_email(gmail_message(subject="Assignment 2 posted", body="")), settings)
    assert not is_assignment_email(parse_email(gmail_message(subject="Travel plans", body="See you soon")), settings)


@pytest.mark.parametrize("sender,accepted", [
    ("Teacher <person@example.edu>", True), ("person@dept.example.edu", True),
    ("person@example.edu.evil.test", False), ("LMS <notifier@lms.test>", True),
    ("lecturer@personal.test", True), ("course-ai@notify.test", True), ("stranger@test.invalid", False),
])
def test_sender_filters(tmp_path, sender, accepted):
    settings = GmailSettings(tmp_path, university_domain="example.edu", lms_sender="notifier@lms.test",
                             teacher_emails=("lecturer@personal.test",), sender_patterns=("course-*@notify.test",))
    assert sender_allowed(sender, settings) is accepted
    assert sender_allowed(sender, GmailSettings(tmp_path))


def test_plain_email_extraction(gmail_message):
    email = parse_email(gmail_message())
    record = assignment_from_email(email)
    assert email.sender == "Test Teacher <teacher@example.edu>"
    assert email.received_at.tzinfo is not None
    assert record.course == "Artificial Intelligence"
    assert record.title == "Assignment 3"
    assert record.teacher == "Test Teacher"
    assert record.deadline == datetime(2026, 9, 20, 18, 59, tzinfo=timezone.utc)
    assert record.lms_url == "https://lms.example.edu/assignment/3"
    assert record.source is Source.GMAIL
    assert record.external_message_id == email.message_id
    assert record.uploaded_at is None
    assert record.needs_review
    assert "Received:" in record.description


def test_html_and_safe_multipart(gmail_message):
    plain = gmail_message(body="Course: Synthetic\nDeadline: 2026-09-20 23:59 PKT")
    html = gmail_message(mime="text/html", body='<p>Course: Synthetic</p><p>Deadline: 2026-09-20 23:59 PKT</p>'
                        '<script>malicious()</script><a href="https://lms.example.edu/assignment/1">Open task</a>'
                        '<a href="javascript:malicious()">Bad</a>')
    email = parse_email(html)
    assert "malicious()" not in email.body
    assert email.links == ("https://lms.example.edu/assignment/1",)
    assert assignment_from_email(email).deadline is not None
    mixed = gmail_message()
    mixed["payload"].update(mimeType="multipart/mixed", parts=[
        {"mimeType": "multipart/alternative", "parts": [plain["payload"], html["payload"]]},
        {"mimeType": "text/plain", "filename": "do-not-read.txt", "body": {"data": "bad-data"}},
    ])
    email = parse_email(mixed)
    assert email.body.count("Course: Synthetic") == 1
    assert "do-not-read" not in email.body


def test_inline_body_loader(gmail_message):
    from unittest.mock import Mock
    message = gmail_message()
    message["payload"]["body"] = {"attachmentId": "inline-text", "size": 100}
    loader = Mock(return_value=base64.urlsafe_b64encode(b"Assignment instructions").decode())
    assert parse_email(message, loader).body == "Assignment instructions"
    loader.assert_called_once_with("inline-text")


@pytest.mark.parametrize("text,expected", [
    ("Deadline: September 20, 2026 at 11:59 PM", datetime(2026, 9, 20, 18, 59, tzinfo=timezone.utc)),
    ("Due date: 20 Sep 2026 23:59 PKT", datetime(2026, 9, 20, 18, 59, tzinfo=timezone.utc)),
    ("Submit by 2026-09-20T23:59:00Z", datetime(2026, 9, 20, 23, 59, tzinfo=timezone.utc)),
    ("Deadline: 2026-09-20 23:59 +05:00", datetime(2026, 9, 20, 18, 59, tzinfo=timezone.utc)),
    ("Deadline: 2026-09-20", None), ("Deadline: Sep 20 at 11 PM", None),
    ("Deadline: 09/10/2026 11 PM", None), ("Due tomorrow", None),
    ("Deadline: 2026-09-20 23:59 EST", None),
    ("Deadline: 2026-09-20 23:59\nDeadline: 2026-09-21 23:59", None),
    ("Deadline: 2026-02-30 23:59", None), ("Deadline: 2026-09-20 25:90", None),
])
def test_deadlines(text, expected):
    assert extract_deadline(text) == expected


def test_uncertain_missing_body_does_not_invent(gmail_message):
    record = assignment_from_email(parse_email(gmail_message(subject="Assignment posted", body="")))
    assert record.course == "Unknown course"
    assert record.title == "Assignment posted"
    assert record.deadline is None
    assert record.teacher == ""
    assert record.needs_review


def test_marketing_filter_keeps_real_assignment(gmail_message, tmp_path):
    message = gmail_message(subject="Special offer for your next project", body="Newsletter discount")
    message["payload"]["headers"].append({"name": "List-Unsubscribe", "value": "<https://example.invalid/unsubscribe>"})
    assert not is_assignment_email(parse_email(message), GmailSettings(tmp_path))
    message["payload"]["headers"][0]["value"] = "Assignment posted to the course mailing list"
    assert is_assignment_email(parse_email(message), GmailSettings(tmp_path))


def test_malformed_and_oversized_email(gmail_message):
    message = gmail_message()
    message["payload"]["body"]["data"] = "!!!"
    with pytest.raises(MalformedEmailError):
        parse_email(message)
    message["payload"]["body"]["data"] = "a" * 2_000_001
    with pytest.raises(MalformedEmailError):
        parse_email(message)


def test_parenthesized_timezone():
    assert extract_deadline("Deadline: 2026-09-20 23:59 (UTC)") == datetime(2026, 9, 20, 23, 59, tzinfo=timezone.utc)
