"""Configuration from environment variables; no secrets or network access."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Local storage paths, resolved independently of the working directory."""

    data_dir: Path
    log_dir: Path

    @property
    def database_path(self) -> Path:
        return self.data_dir / "assignments.db"


def _directory(variable: str, default: str) -> Path:
    value = os.environ.get(variable, default).strip()
    if not value:
        raise ValueError(f"{variable} must not be empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_settings() -> Settings:
    """Read settings without creating files or loading .env implicitly."""
    return Settings(
        data_dir=_directory("ASSIGNMENT_DATA_DIR", "data"),
        log_dir=_directory("ASSIGNMENT_LOG_DIR", "logs"),
    )


DEFAULT_ASSIGNMENT_SCORE_THRESHOLD = 6

DEFAULT_GMAIL_KEYWORDS = (
    "assignment", "new assignment", "assignment uploaded", "assignment posted",
    "deadline", "due date", "submission", "homework", "coursework", "quiz", "project", "LMS",
)


@dataclass(frozen=True)
class GmailSettings:
    credentials_dir: Path
    keywords: tuple[str, ...] = DEFAULT_GMAIL_KEYWORDS
    scan_days: int = 7
    max_messages: int = 100
    fetch_delay_ms: int = 100
    max_retries: int = 4
    unread_only: bool = False
    search_terms: tuple[str, ...] = ("assignment", "assignments", "homework", "coursework", "quiz", "assessment", "graded task")
    discovery_terms: tuple[str, ...] = ("deadline", "due", "submission", "LMS", "course", "class")
    fallback_messages: int = 25
    university_domain: str = ""
    lms_sender: str = ""
    teacher_emails: tuple[str, ...] = ()
    sender_patterns: tuple[str, ...] = ()
    trusted_senders: tuple[str, ...] = ()
    trusted_domains: tuple[str, ...] = ()
    score_threshold: int = DEFAULT_ASSIGNMENT_SCORE_THRESHOLD

    @property
    def credentials_path(self) -> Path:
        return self.credentials_dir / "gmail_credentials.json"

    @property
    def token_path(self) -> Path:
        return self.credentials_dir / "gmail_token.json"


def _csv(variable: str, default: str = "") -> tuple[str, ...]:
    return tuple(value.strip() for value in os.environ.get(variable, default).split(",") if value.strip())


def load_gmail_settings() -> GmailSettings:
    """Read Gmail-only settings lazily, so they cannot disrupt local reminders."""
    threshold = int(os.environ.get("ASSIGNMENT_SCORE_THRESHOLD", str(DEFAULT_ASSIGNMENT_SCORE_THRESHOLD)))
    if not 1 <= threshold <= 100:
        raise ValueError("ASSIGNMENT_SCORE_THRESHOLD must be 1..100")
    days = int(os.environ.get("GMAIL_SCAN_DAYS", "7"))
    maximum = int(os.environ.get("GMAIL_MAX_MESSAGES", "100"))
    delay = int(os.environ.get("GMAIL_FETCH_DELAY_MS", "100"))
    retries = int(os.environ.get("GMAIL_MAX_RETRIES", "4"))
    unread = os.environ.get("GMAIL_UNREAD_ONLY", "false").strip().lower()
    terms = _csv("GMAIL_SEARCH_TERMS", "assignment,assignments,homework,coursework,quiz,assessment,graded task")
    if not 0 <= delay <= 5000 or not 0 <= retries <= 6:
        raise ValueError("GMAIL_FETCH_DELAY_MS must be 0..5000 and GMAIL_MAX_RETRIES 0..6")
    if unread not in ("true", "false"):
        raise ValueError("GMAIL_UNREAD_ONLY must be true or false")
    if not terms:
        raise ValueError("GMAIL_SEARCH_TERMS must contain at least one term")
    discovery_terms = _csv("GMAIL_DISCOVERY_TERMS", "deadline,due,submission,LMS,course,class")
    fallback = int(os.environ.get("GMAIL_FALLBACK_MESSAGES", "25"))
    if not discovery_terms or not 0 <= fallback <= 50:
        raise ValueError("GMAIL_DISCOVERY_TERMS must be nonempty and GMAIL_FALLBACK_MESSAGES must be 0..50")
    keywords = _csv("GMAIL_KEYWORDS", ",".join(DEFAULT_GMAIL_KEYWORDS))
    if not 1 <= days <= 365 or not 1 <= maximum <= 10000:
        raise ValueError("GMAIL_SCAN_DAYS must be 1..365 and GMAIL_MAX_MESSAGES 1..10000")
    if not keywords:
        raise ValueError("GMAIL_KEYWORDS must contain at least one keyword")
    return GmailSettings(
        credentials_dir=_directory("GMAIL_CREDENTIALS_DIR", "credentials"),
        keywords=keywords, scan_days=days, max_messages=maximum,
        fetch_delay_ms=delay, max_retries=retries, unread_only=unread == "true", search_terms=terms,
        discovery_terms=discovery_terms, fallback_messages=fallback,
        university_domain=os.environ.get("GMAIL_UNIVERSITY_DOMAIN", "").strip().lower().lstrip("@"),
        lms_sender=os.environ.get("GMAIL_LMS_SENDER", "").strip().lower(),
        teacher_emails=_csv("GMAIL_TEACHER_EMAILS"), sender_patterns=_csv("GMAIL_SENDER_PATTERNS"),
        trusted_senders=_csv("TRUSTED_ACADEMIC_SENDERS"), trusted_domains=_csv("TRUSTED_ACADEMIC_DOMAINS"),
        score_threshold=threshold,
    )
