from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock

import pytest

from app.database import Database
from app.models import Assignment, LOCAL_TIMEZONE, Status
from app.notifier import Notifier, NotificationError
from app.scheduler import check_reminders, daily_summary, summary_groups

NOW = datetime(2030, 9, 16, 20, tzinfo=LOCAL_TIMEZONE)


def add(database, *, delta=timedelta(days=1), **changes):
    values = dict(course="Synthetic CS", title="Synthetic task", deadline=NOW + delta)
    values.update(changes)
    return database.create_assignment(Assignment(**values))


@pytest.fixture
def notifier():
    return Mock(spec=Notifier)


@pytest.mark.parametrize("day,kind,title,urgent", [
    (19, "DUE_TOMORROW", "Assignment Due Tomorrow", False),
    (20, "DUE_TODAY", "Assignment Due Today", True),
])
def test_calendar_day_reminders(database, notifier, day, kind, title, urgent):
    deadline = datetime(2026, 9, 20, 23, 59, tzinfo=LOCAL_TIMEZONE)
    record = add(database, deadline=deadline, title="AI Assignment 2")
    # At local midnight, it is still the previous date in UTC.
    now = datetime(2026, 9, day, 0, 0, tzinfo=LOCAL_TIMEZONE).astimezone(timezone.utc)
    result = check_reminders(database, notifier, now)
    assert result.sent == 1
    day_word = "today" if urgent else "tomorrow"
    notifier.send.assert_called_once_with(title,
        f"AI Assignment 2 is due {day_word} at 11:59 PM.\nCourse: Synthetic CS (Asia/Karachi)", urgent=urgent)
    event = database.list_reminder_events(record.id)[0]
    assert event["reminder_type"] == kind
    assert datetime.fromisoformat(event["sent_at"]).utcoffset() == timedelta(0)


def test_duplicate_prevention_survives_reopening(database, notifier):
    record = add(database)
    assert check_reminders(database, notifier, NOW).sent == 1
    reopened = Database(database.path)
    assert check_reminders(reopened, notifier, NOW + timedelta(minutes=5)).skipped == 1
    assert len(reopened.list_reminder_events(record.id)) == 1
    notifier.send.assert_called_once()


def test_progression_sends_exactly_two_reminders(database, notifier):
    record = add(database)
    due_date = record.deadline.astimezone(LOCAL_TIMEZONE).replace(hour=0, minute=0)
    for now in (due_date - timedelta(days=1), due_date):
        assert check_reminders(database, notifier, now).sent == 1
        assert check_reminders(database, notifier, now + timedelta(minutes=5)).sent == 0
    assert check_reminders(database, notifier, due_date + timedelta(days=1)).sent == 0
    assert notifier.send.call_count == 2
    assert {e["reminder_type"] for e in database.list_reminder_events(record.id)} == {"DUE_TODAY", "DUE_TOMORROW"}


def test_no_three_day_reminder(database, notifier):
    add(database, delta=timedelta(days=3))
    assert check_reminders(database, notifier, NOW).sent == 0
    notifier.send.assert_not_called()


@pytest.mark.parametrize("hours", [6, 1])
def test_no_extra_six_or_one_hour_reminder(database, notifier, hours):
    record = add(database, deadline=NOW.replace(hour=23, minute=59))
    assert check_reminders(database, notifier, NOW.replace(hour=0)).sent == 1
    assert check_reminders(database, notifier, record.deadline - timedelta(hours=hours)).sent == 0
    notifier.send.assert_called_once()
    assert database.list_reminder_events(record.id)[0]["reminder_type"] == "DUE_TODAY"


@pytest.mark.parametrize("days", [0, 1, -1])
def test_submitted_ignored_on_every_reminder_date(database, notifier, days):
    add(database, delta=timedelta(days=days), status=Status.SUBMITTED)
    assert check_reminders(database, notifier, NOW).sent == 0
    notifier.send.assert_not_called()


def test_no_separate_overdue_alert(database, notifier):
    add(database, delta=timedelta(days=-1))
    assert check_reminders(database, notifier, NOW).sent == 0
    notifier.send.assert_not_called()


def test_deadline_earlier_today_still_gets_calendar_day_reminder(database, notifier):
    record = add(database, deadline=NOW - timedelta(hours=1))
    assert check_reminders(database, notifier, NOW).sent == 1
    assert database.list_reminder_events(record.id)[0]["reminder_type"] == "DUE_TODAY"
    assert database.get_assignment(record.id).status is Status.OVERDUE


def test_submitted_unknown_and_far_future_are_skipped(database, notifier):
    add(database, title="Submitted", status=Status.SUBMITTED, delta=timedelta(days=-1))
    add(database, title="Unknown", deadline=None)
    add(database, title="Far future", delta=timedelta(days=3, seconds=1))
    assert check_reminders(database, notifier, NOW).sent == 0
    notifier.send.assert_not_called()


def test_failed_notification_retries_without_blocking_others(database, notifier):
    first = add(database, title="First", delta=timedelta(hours=1))
    second = add(database, title="Second")
    notifier.send.side_effect = [NotificationError("Synthetic failure"), None]
    result = check_reminders(database, notifier, NOW)
    assert result.sent == 1 and len(result.errors) == 1
    assert database.list_reminder_events(first.id) == []
    assert len(database.list_reminder_events(second.id)) == 1
    notifier.send.side_effect = None
    assert check_reminders(database, notifier, NOW).sent == 1


def test_changed_deadline_new_history_and_delete_cleanup(database, notifier):
    record = add(database)
    check_reminders(database, notifier, NOW)
    database.update_assignment(record.id, deadline=record.deadline - timedelta(hours=1))
    assert check_reminders(database, notifier, NOW).sent == 1
    assert len(database.list_reminder_events(record.id)) == 2
    database.delete_assignment(record.id)
    assert database.list_reminder_events(record.id) == []
    # SQLite can reuse an assignment ID; old reminders must not suppress the replacement.
    replacement = add(database)
    assert check_reminders(database, notifier, NOW).sent == 1
    assert len(database.list_reminder_events(replacement.id)) == 1


@pytest.mark.parametrize("change", ["submitted", "deadline", "deleted"])
def test_recheck_assignment_under_write_lock(database, notifier, change):
    record = add(database)
    if change == "submitted":
        database.mark_submitted(record.id)
    elif change == "deadline":
        database.update_assignment(record.id, deadline=NOW + timedelta(days=4))
    else:
        database.delete_assignment(record.id)
    assert not database.send_reminder_once(record, "DUE_TOMORROW", notifier.send, NOW)
    notifier.send.assert_not_called()


def test_concurrent_checkers_send_once(database, notifier):
    record = add(database)
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return database.send_reminder_once(record, "DUE_TOMORROW", lambda _: notifier.send("Test", "Body"), NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert sorted(results) == [False, True]
    notifier.send.assert_called_once()


def test_additive_migration_preserves_records(database):
    record = add(database)
    with database._connect() as connection:
        connection.execute("DROP TABLE reminder_events")
    migrated = Database(database.path)
    migrated.initialize()
    assert migrated.get_assignment(record.id) == record
    assert migrated.list_reminder_events(record.id) == []


def test_daily_summary_karachi_calendar_and_overlapping_groups(database):
    unknown = add(database, title="New without deadline", deadline=None)
    # Tomorrow in Karachi, but still today in UTC.
    tomorrow = add(database, title="Tomorrow", deadline=datetime(2030, 9, 16, 20, tzinfo=timezone.utc),
                   status=Status.REVIEWED)
    within = add(database, title="Within three", delta=timedelta(days=3), status=Status.IN_PROGRESS)
    past = add(database, title="Past", delta=timedelta(seconds=-1), status=Status.IN_PROGRESS)
    beyond = add(database, title="Beyond", delta=timedelta(days=3, seconds=1), status=Status.REVIEWED)
    add(database, title="Submitted", status=Status.SUBMITTED)
    groups = summary_groups(database, NOW)
    assert groups["New Assignments"] == [unknown]
    assert groups["Due Tomorrow"] == [tomorrow]
    assert groups["Due Within 3 Days"] == [tomorrow, within]
    assert groups["Overdue"] == [past]
    assert {a.id for a in groups["Not Submitted"]} == {unknown.id, tomorrow.id, within.id, past.id, beyond.id}
    output = daily_summary(database, NOW)
    assert "ASSIGNMENT SUMMARY" in output and "Asia/Karachi" in output
    assert "Sep 17, 2030, 01:00 AM" in output
    assert "Synthetic CS: Submitted" not in output
    assert database.get_assignment(past.id).status is Status.IN_PROGRESS  # Summary is read-only.


def test_empty_summary(database):
    assert daily_summary(database, NOW).count("* None") == 5


def test_naive_clock_rejected(database, notifier):
    with pytest.raises(ValueError):
        check_reminders(database, notifier, datetime(2030, 1, 1))
    notifier.send.assert_not_called()


@pytest.mark.parametrize("kind", ["3_DAYS", "1_DAY", "6_HOURS", "1_HOUR", "OVERDUE"])
def test_old_types_rejected(database, notifier, kind):
    import sqlite3
    record = add(database)
    with pytest.raises(ValueError, match="Unknown reminder type"):
        database.send_reminder_once(record, kind, notifier.send, NOW)
    with pytest.raises(sqlite3.IntegrityError):
        with database._connect() as connection:
            connection.execute("INSERT INTO reminder_events VALUES (?, ?, ?, ?)",
                               (record.id, record.deadline.isoformat(), kind, NOW.isoformat()))
    notifier.send.assert_not_called()


def test_legacy_migration_preserves_data_and_prevents_same_day_duplicates(database, notifier):
    record = add(database, deadline=datetime(2026, 9, 20, 23, 59, tzinfo=LOCAL_TIMEZONE))
    deadline = record.deadline.isoformat(timespec="microseconds")
    events = [
        ("3_DAYS", datetime(2026, 9, 17, 23, 59, tzinfo=LOCAL_TIMEZONE)),
        ("1_DAY", datetime(2026, 9, 19, 23, 59, tzinfo=LOCAL_TIMEZONE)),
        ("6_HOURS", datetime(2026, 9, 20, 17, 59, tzinfo=LOCAL_TIMEZONE)),
        ("1_HOUR", datetime(2026, 9, 20, 22, 59, tzinfo=LOCAL_TIMEZONE)),
        ("OVERDUE", datetime(2026, 9, 21, 0, 1, tzinfo=LOCAL_TIMEZONE)),
    ]
    with database._connect() as connection:
        connection.execute("DROP TABLE reminder_events")
        connection.execute("""CREATE TABLE reminder_events (
            assignment_id INTEGER REFERENCES assignments(id) ON DELETE CASCADE,
            deadline TEXT NOT NULL,
            reminder_type TEXT CHECK(reminder_type IN ('3_DAYS','1_DAY','6_HOURS','1_HOUR','OVERDUE')),
            sent_at TEXT NOT NULL, PRIMARY KEY(assignment_id, deadline, reminder_type))""")
        connection.executemany("INSERT INTO reminder_events VALUES (?, ?, ?, ?)",
            [(record.id, deadline, kind, sent.astimezone(timezone.utc).isoformat(timespec="microseconds"))
             for kind, sent in events])
    migrated = Database(database.path)
    migrated.initialize()  # Repeated initialization must not reimport archived history.
    assert migrated.get_assignment(record.id) == record
    assert {e["reminder_type"] for e in migrated.list_reminder_events(record.id)} == {"DUE_TOMORROW", "DUE_TODAY"}
    with migrated._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reminder_events_legacy").fetchone()[0] == 5
    for day in (19, 20):
        assert check_reminders(migrated, notifier,
            datetime(2026, 9, day, 23, 59, tzinfo=LOCAL_TIMEZONE)).sent == 0
    notifier.send.assert_not_called()
