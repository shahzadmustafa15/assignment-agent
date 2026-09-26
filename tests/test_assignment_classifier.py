from dataclasses import replace

import pytest

from app.assignment_classifier import classify_email, trusted_sender
from app.assignment_parser import parse_email
from app.config import GmailSettings, load_gmail_settings


@pytest.mark.parametrize("subject,expected", [
    ("Fee Payment Due Date Passed", "REJECTED"),
    ("Fee Payment Due Date - Warning", "REJECTED"),
    ("Real reviews. Real results. Meet My Notes.", "REJECTED"),
    ("Compiler Construction Assignment 2 Uploaded", "ASSIGNMENT"),
    ("AI Lab Assignment Deadline: September 22", "ASSIGNMENT"),
    ("Your tuition payment is due tomorrow", "REJECTED"),
    ("Assignment 3 is due tomorrow", "ASSIGNMENT"),
    ("deadline", "REJECTED"),
    ("Project sale discount special offer", "REJECTED"),
    ("Newsletter: choose your next course", "REJECTED"),
])
def test_required_cases(gmail_message, tmp_path, subject, expected):
    result = classify_email(parse_email(gmail_message(subject=subject, body="", sender="unknown@unknown.test")), GmailSettings(tmp_path))
    assert result.classification == expected
    assert type(result.score) is int and isinstance(result.reasons, list) and result.reasons


def test_fee_explanation(gmail_message, tmp_path):
    result = classify_email(parse_email(gmail_message(subject="Fee Payment Due Date Passed", body="")), GmailSettings(tmp_path))
    assert result.score == -10
    assert any("fee terminology" in reason for reason in result.reasons)
    assert any("payment terminology" in reason for reason in result.reasons)
    assert any("no assignment-specific" in reason for reason in result.reasons)


@pytest.mark.parametrize("generic", ["due", "due date", "deadline", "notes", "project", "submission", "course",
                                      "class lecture semester instructor teacher faculty section marks grade"])
def test_generic_and_weak_words_cannot_pass_even_if_trusted(gmail_message, tmp_path, generic):
    settings = GmailSettings(tmp_path, trusted_domains=("example.edu",), score_threshold=1)
    result = classify_email(parse_email(gmail_message(subject=generic, body="")), settings)
    assert result.classification == "REJECTED"


def test_trusted_posted_and_unknown_needs_more_evidence(gmail_message, tmp_path):
    settings = GmailSettings(tmp_path, trusted_senders=("teacher@example.edu",))
    email = parse_email(gmail_message(subject="Assignment 4 posted", body=""))
    assert classify_email(email, settings).classification == "ASSIGNMENT"
    minimal = parse_email(gmail_message(subject="Assignment", body=""))
    trusted = classify_email(minimal, settings)
    unknown = classify_email(minimal, replace(settings, trusted_senders=()))
    assert trusted.classification == "ASSIGNMENT" and unknown.classification == "REJECTED"
    assert trusted.threshold == 6 and unknown.threshold == 8
    with_context = parse_email(gmail_message(subject="Assignment uploaded", body="Course: Synthetic\nInstructor: Test"))
    assert classify_email(with_context, GmailSettings(tmp_path)).classification == "ASSIGNMENT"


@pytest.mark.parametrize("sender,expected", [
    ("person@example.edu", True), ("person@dept.example.edu", True),
    ("person@example.edu.attacker.test", False), ("person@fakeexample.edu", False),
    ('"teacher@example.edu" <attacker@test.invalid>', False),
    ("course-ai@notifications.test", True),
])
def test_trust_patterns_are_address_based(tmp_path, sender, expected):
    settings = GmailSettings(tmp_path, trusted_senders=("course-*@notifications.test",), trusted_domains=("example.edu",))
    assert trusted_sender(sender, settings) is expected


def test_threshold_and_configuration(monkeypatch, gmail_message):
    monkeypatch.setenv("ASSIGNMENT_SCORE_THRESHOLD", "30")
    monkeypatch.setenv("TRUSTED_ACADEMIC_SENDERS", " teacher@example.edu,course-*@example.test ")
    monkeypatch.setenv("TRUSTED_ACADEMIC_DOMAINS", "example.edu,*.example.test")
    settings = load_gmail_settings()
    assert settings.score_threshold == 30
    assert settings.trusted_senders == ("teacher@example.edu", "course-*@example.test")
    assert classify_email(parse_email(gmail_message()), settings).classification == "REJECTED"
    monkeypatch.setenv("ASSIGNMENT_SCORE_THRESHOLD", "0")
    with pytest.raises(ValueError, match="ASSIGNMENT_SCORE_THRESHOLD"):
        load_gmail_settings()


def test_repeated_words_do_not_inflate_score(gmail_message, tmp_path):
    settings = GmailSettings(tmp_path)
    once = classify_email(parse_email(gmail_message(subject="Assignment", body="course teacher")), settings)
    repeated = classify_email(parse_email(gmail_message(subject="Assignment " * 100, body="course teacher " * 100)), settings)
    assert once.score == repeated.score


def test_trust_does_not_override_existing_sender_filter(gmail_message, tmp_path):
    settings = GmailSettings(tmp_path, trusted_domains=("example.edu",), university_domain="different.test")
    result = classify_email(parse_email(gmail_message()), settings)
    assert result.classification == "REJECTED"
    assert "excluded by configured sender filter" in result.reasons
