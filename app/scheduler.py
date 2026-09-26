"""One-shot local reminder checking and summaries; no background thread."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.database import Database
from app.models import Assignment, LOCAL_TIMEZONE, Status, aware_utc, utc_now
from app.notifier import Notifier, NotificationError


def reminder_window(assignment: Assignment, now: datetime) -> tuple[str, str] | None:
    """Choose one reminder by Karachi calendar date, including the whole due day."""
    now = aware_utc(now)
    if assignment.status is Status.SUBMITTED or assignment.deadline is None:
        return None
    today = now.astimezone(LOCAL_TIMEZONE).date()
    due_date = assignment.deadline.astimezone(LOCAL_TIMEZONE).date()
    if due_date == today:
        return "DUE_TODAY", "Assignment Due Today"
    if due_date == today + timedelta(days=1):
        return "DUE_TOMORROW", "Assignment Due Tomorrow"
    return None


def deadline_text(assignment: Assignment) -> str:
    if assignment.deadline is None:
        return "Unknown (needs review)"
    return assignment.deadline.astimezone(LOCAL_TIMEZONE).strftime("%b %d, %Y, %I:%M %p %Z")


@dataclass
class CheckResult:
    sent: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def check_reminders(database: Database, notifier: Notifier,
                    now: datetime | None = None) -> CheckResult:
    now = aware_utc(now if now is not None else utc_now())
    database.mark_overdue(now)
    result = CheckResult()
    # Calendar-day reminders include a deadline earlier today, but never past dates.
    for assignment in database.list_assignments():
        window = reminder_window(assignment, now)
        if window is None:
            continue
        kind, label = window

        def send(current: Assignment) -> None:
            due_time = current.deadline.astimezone(LOCAL_TIMEZONE).strftime("%I:%M %p").lstrip("0")
            day = "today" if kind == "DUE_TODAY" else "tomorrow"
            notifier.send(label, f"{current.title} is due {day} at {due_time}.\n"
                          f"Course: {current.course} (Asia/Karachi)", urgent=kind == "DUE_TODAY")

        try:
            if database.send_reminder_once(assignment, kind, send, now):
                result.sent += 1
            else:
                result.skipped += 1
        except NotificationError as exc:
            result.errors.append(f"Assignment {assignment.id}: {exc}")
    # Independent channel: a successful desktop event never suppresses email,
    # and a desktop failure cannot prevent an eligible email.
    from app.email_notifications import notify_due
    notify_due(database, now)
    return result


def summary_groups(database: Database, now: datetime | None = None,
                   student_id: str | None = None) -> dict[str, list[Assignment]]:
    now = aware_utc(now if now is not None else utc_now())
    tomorrow = now.astimezone(LOCAL_TIMEZONE).date() + timedelta(days=1)
    pending = [
        item
        for item in database.list_assignments(student_id=student_id)
        if item.status is not Status.SUBMITTED
    ]
    return {
        "New Assignments": [item for item in pending if item.status is Status.NEW],
        "Due Tomorrow": [item for item in pending if item.deadline is not None
                         and item.deadline.astimezone(LOCAL_TIMEZONE).date() == tomorrow],
        "Due Within 3 Days": [item for item in pending if item.deadline is not None
                              and now <= item.deadline <= now + timedelta(days=3)],
        "Overdue": [item for item in pending if item.deadline is not None and item.deadline < now],
        "Not Submitted": pending,
    }


def daily_summary(database: Database, now: datetime | None = None,
                  student_id: str | None = None) -> str:
    lines = ["ASSIGNMENT SUMMARY", "Timezone: Asia/Karachi"]
    for heading, assignments in summary_groups(
        database,
        now,
        student_id=student_id,
    ).items():
        lines.extend(["", f"{heading}:"])
        if not assignments:
            lines.append("* None")
        for assignment in assignments:
            lines.extend([f"* {assignment.course}: {assignment.title}",
                          f"  Deadline: {deadline_text(assignment)}"])
    return "\n".join(lines)
