"""Two-stage manual Gmail scan with bounded retries and durable imports."""

from dataclasses import dataclass, field, replace
import re
import time

from app.assignment_parser import MalformedEmailError, assignment_from_email, parse_email, sender_allowed
from app.assignment_classifier import (ClassificationResult, NEGATIVE_SIGNALS, WEAK_SIGNALS,
                                       classify_email, trusted_sender)
from app.config import GmailSettings
from app.database import Database
from app.gmail_auth import GmailError, api_execute, safe_message_id
from app.gmail_retry import RetryStats
from app.gmail_discovery import QueryReport, candidate_query, discover_messages
from app.notifier import Notifier, NotificationError
from app.scheduler import deadline_text

METADATA_HEADERS = ["Subject", "From", "List-Unsubscribe"]
TASK_HINT = re.compile(r"\b(?:assignments?|homework|coursework|quiz|assessment|graded\s+task)\b", re.I)


@dataclass(frozen=True)
class ClassificationReport:
    subject: str
    sender: str
    result: ClassificationResult


@dataclass
class ScanResult:
    examined: int = 0
    candidate_ids: int = 0
    metadata_fetched: int = 0
    metadata_candidates: int = 0
    fallback_used: bool = False
    discovery_duplicates: int = 0
    discovery_reports: list[QueryReport] = field(default_factory=list)
    full_fetched: int = 0
    created: int = 0
    accepted: int = 0
    skipped: int = 0
    ignored: int = 0
    notified: int = 0
    retries: int = 0
    rate_limit_failures: int = 0
    truncated: bool = False
    errors: list[str] = field(default_factory=list)
    classifications: list[ClassificationReport] = field(default_factory=list)

    @property
    def other_errors(self) -> int:
        return len(self.errors) - self.rate_limit_failures



def metadata_email(raw, message_id):
    if not isinstance(raw, dict) or raw.get("id") != message_id or not isinstance(raw.get("payload", {}), dict):
        raise MalformedEmailError("Malformed message metadata")
    email = parse_email({"id": message_id, "internalDate": raw.get("internalDate"), "payload": {
        "mimeType": "text/plain", "headers": raw.get("payload", {}).get("headers", []), "body": {},
    }})
    snippet = raw.get("snippet", "")
    if not isinstance(snippet, str):
        raise MalformedEmailError("Malformed message snippet")
    return replace(email, body=snippet[:1000])


def metadata_relevant(email, settings):
    """Cheap relevance gate; full-body classification still decides acceptance."""
    if not sender_allowed(email.sender, settings):
        return False
    text = email.subject + "\n" + email.body
    if TASK_HINT.search(text):
        return True
    # Financial/promotional subject without task evidence is not worth a full fetch.
    if any(re.search(r"\b(?:" + pattern + r")\b", email.subject, re.I)
           for pattern in NEGATIVE_SIGNALS.values()):
        return False
    return bool(re.search(r"\b(?:deadline|due|submission|LMS)\b", text, re.I)) or trusted_sender(email.sender, settings) or any(
        re.search(r"\b" + re.escape(signal) + r"\b", text, re.I) for signal in WEAK_SIGNALS
    )


def scan_gmail(service, database: Database | None, settings: GmailSettings, notifier: Notifier,
               *, dry_run: bool = False, verbose: bool = False) -> ScanResult:
    """Search IDs, inspect headers/snippets, then fetch only relevant full bodies."""
    if not dry_run and database is None:
        raise ValueError("A database is required for a non-dry scan")
    result = ScanResult()
    retry_stats = RetryStats()
    messages = service.users().messages()
    progress = (lambda message: print(message, flush=True)) if verbose else None
    full_attempts = 0

    def execute(request, message_id=None):
        return api_execute(request, message_id=message_id, max_retries=settings.max_retries,
                           retry_stats=retry_stats, progress=progress)

    def record(email, classification, *, metadata_only=False):
        accepted = classification.classification == "ASSIGNMENT" and not metadata_only
        result.accepted += int(accepted)
        if (dry_run or verbose) and classification.likely_candidate:
            result.classifications.append(ClassificationReport(email.subject, email.sender, classification))
        if dry_run:
            result.ignored += int(not accepted)
            return
        assignment = assignment_from_email(email) if accepted else None
        outcome = database.record_gmail_message(email.message_id, email.subject, email.sender,
                                                email.received_at, assignment)
        if outcome == "created":
            result.created += 1
        elif outcome == "ignored":
            result.ignored += 1
        else:
            result.skipped += 1

    discovery = discover_messages(messages, settings, execute)
    result.candidate_ids = len(discovery.ids)
    result.discovery_reports = discovery.queries
    result.discovery_duplicates = discovery.duplicates
    result.fallback_used = discovery.fallback_used
    result.truncated = discovery.truncated
    result.errors.extend(discovery.errors)
    for message_id in discovery.ids:
        result.examined += 1
        if not dry_run and database.gmail_message_processed(message_id):
            result.skipped += 1
            continue
        try:
            metadata = execute(messages.get(userId="me", id=message_id, format="metadata",
                metadataHeaders=METADATA_HEADERS, fields="id,internalDate,snippet,payload/headers"), message_id)
            result.metadata_fetched += 1
            preview = metadata_email(metadata, message_id)
            if not metadata_relevant(preview, settings):
                classification = classify_email(preview, settings)
                classification = replace(classification, classification="REJECTED", reasons=[
                    *classification.reasons, "metadata prefilter: no relevant subject/snippet/sender; full body not fetched"])
                record(preview, classification, metadata_only=True)
                continue
            result.metadata_candidates += 1
            if full_attempts and settings.fetch_delay_ms:
                time.sleep(settings.fetch_delay_ms / 1000)
            full_attempts += 1
            raw = execute(messages.get(userId="me", id=message_id, format="full"), message_id)
            result.full_fetched += 1
            if not isinstance(raw, dict) or raw.get("id") != message_id:
                raise MalformedEmailError("Message identifier mismatch")
            def load_inline_body(attachment_id):
                return execute(messages.attachments().get(
                    userId="me", messageId=message_id, id=attachment_id), message_id).get("data", "")
            email = parse_email(raw, attachment_loader=load_inline_body)
            record(email, classify_email(email, settings))
        except (MalformedEmailError, GmailError) as exc:
            result.errors.append(f"Message {safe_message_id(message_id)}: {exc}")
    result.retries = retry_stats.retries
    result.rate_limit_failures = retry_stats.rate_limit_failures
    if dry_run:
        return result
    for message_id in database.pending_gmail_notifications():
        def send(assignment):
            notifier.send("New Assignment Detected", f"{assignment.course} — {assignment.title}\n"
                          f"Due: {deadline_text(assignment)}\nPlease review extracted details.")
        try:
            result.notified += int(database.notify_gmail_once(message_id, send))
        except NotificationError as exc:
            result.errors.append(f"New assignment saved; notification will retry on the next scan: {exc}")
    return result
