"""Validated assignment records and timezone helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE = ZoneInfo("Asia/Karachi")


class Status(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    IN_PROGRESS = "IN_PROGRESS"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    OVERDUE = "OVERDUE"


class Source(str, Enum):
    GMAIL = "gmail"
    LMS = "lms"
    MANUAL = "manual"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Datetime values must include a timezone")
    return value.astimezone(timezone.utc)


def normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class Assignment:
    course: str
    title: str
    student_id: str = "shahzad"
    teacher: str = ""
    description: str = ""
    uploaded_at: datetime | None = None
    deadline: datetime | None = None
    lms_url: str | None = None
    source: Source = Source.MANUAL
    external_message_id: str | None = None
    status: Status = Status.NEW
    needs_review: bool = True
    id: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.student_id, str) or not self.student_id.strip():
            raise ValueError("student_id must be non-empty text")
        object.__setattr__(self, "student_id", self.student_id.strip())

        for name in ("course", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
            object.__setattr__(self, name, " ".join(value.split()))
        for name in ("teacher", "description"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
        for name in ("lms_url", "external_message_id"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"{name} must be text or None")
                object.__setattr__(self, name, value.strip() or None)
        object.__setattr__(self, "source", Source(self.source))
        object.__setattr__(self, "status", Status(self.status))
        if type(self.needs_review) is not bool:
            raise ValueError("needs_review must be a boolean")
        for name in ("uploaded_at", "deadline", "created_at", "updated_at"):
            value = getattr(self, name)
            if value is None and name in ("created_at", "updated_at"):
                raise ValueError(f"{name} is required")
            if value is not None:
                object.__setattr__(self, name, aware_utc(value))
