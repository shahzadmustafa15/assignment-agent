from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.database import AssignmentNotFoundError, Database, DuplicateAssignmentError
from app.models import Assignment, Source, Status

NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def sample(**changes):
    return Assignment(**(dict(course="Synthetic CS", title="Practice task", deadline=NOW) | changes))


def test_initialization(database):
    assert database.path.is_file()
    database.initialize()
    assert database.list_assignments() == []


def test_create_read_and_persistence(database):
    record = database.create_assignment(sample(teacher="Test teacher", description="Synthetic only",
        uploaded_at=NOW - timedelta(days=1), lms_url="https://example.invalid/task",
        source=Source.GMAIL, external_message_id="synthetic-001"))
    assert record.id is not None
    assert database.get_assignment(record.id) == record
    assert Database(database.path).get_assignment(record.id) == record
    assert record.created_at.tzinfo is not None
    assert database.assignment_exists(sample())
    assert database.get_assignment(99999) is None


def test_external_identifier_duplicate(database):
    database.create_assignment(sample(source="gmail", external_message_id=" abc "))
    duplicate = sample(source="gmail", external_message_id="abc", title="Changed title",
                       deadline=NOW + timedelta(days=1))
    assert database.assignment_exists(duplicate)
    with pytest.raises(DuplicateAssignmentError):
        database.create_assignment(duplicate)
    # External IDs belong to their source namespace.
    database.create_assignment(replace(duplicate, source=Source.LMS))


@pytest.mark.parametrize("deadline", [NOW, None])
def test_natural_key_duplicate(database, deadline):
    database.create_assignment(sample(deadline=deadline))
    equivalent = deadline.astimezone(ZoneInfo("Asia/Karachi")) if deadline else None
    duplicate = sample(course="  synthetic   CS ", title="PRACTICE task", deadline=equivalent,
                       source="lms", external_message_id="another-source")
    assert database.assignment_exists(duplicate)
    with pytest.raises(DuplicateAssignmentError):
        database.create_assignment(duplicate)
    assert len(database.list_assignments()) == 1


def test_distinct_deadlines_and_blank_identifier(database):
    database.create_assignment(sample(external_message_id=" "))
    record = sample(deadline=NOW + timedelta(days=1))
    assert not database.assignment_exists(record)
    database.create_assignment(record)
    assert len(database.list_assignments()) == 2


def test_status_filter(database):
    first = database.create_assignment(sample())
    second = database.create_assignment(sample(title="Other", status=Status.IN_PROGRESS))
    assert database.list_by_status("NEW") == [first]
    assert database.list_assignments(Status.IN_PROGRESS) == [second]
    with pytest.raises(ValueError):
        database.list_by_status("INVALID")


def test_update(database):
    record = database.create_assignment(sample())
    updated = database.update_assignment(record.id, title="Edited", status="REVIEWED",
                                         needs_review=False, deadline=None)
    assert updated.title == "Edited"
    assert updated.status is Status.REVIEWED
    assert updated.needs_review is False
    assert updated.deadline is None
    assert updated.created_at == record.created_at
    assert updated.updated_at >= record.updated_at
    assert database.get_assignment(record.id) == updated
    with pytest.raises(ValueError):
        database.update_assignment(record.id, id=100)
    with pytest.raises(AssignmentNotFoundError):
        database.update_assignment(9999, title="Missing")


def test_duplicate_update_rolls_back(database):
    first = database.create_assignment(sample())
    second = database.create_assignment(sample(title="Second"))
    with pytest.raises(DuplicateAssignmentError):
        database.update_assignment(second.id, title=first.title)
    assert database.get_assignment(second.id) == second


def test_mark_submitted(database):
    record = database.create_assignment(sample())
    assert database.mark_submitted(record.id).status is Status.SUBMITTED
    assert database.get_assignment(record.id).status is Status.SUBMITTED
    with pytest.raises(AssignmentNotFoundError):
        database.mark_submitted(9999)


def test_overdue_and_upcoming(database):
    past = NOW - timedelta(seconds=1)
    for status in Status:
        database.create_assignment(sample(title=status.value, deadline=past, status=status))
    at_deadline = database.create_assignment(sample(title="At deadline"))
    future = database.create_assignment(sample(title="Future", deadline=NOW + timedelta(days=1)))
    unknown = database.create_assignment(sample(title="Unknown", deadline=None))
    assert database.mark_overdue(NOW.astimezone(ZoneInfo("Asia/Karachi"))) == 4
    assert len(database.list_by_status(Status.OVERDUE)) == 5
    assert len(database.list_by_status(Status.SUBMITTED)) == 1
    assert database.mark_overdue(NOW) == 0
    assert database.upcoming(NOW) == [at_deadline, future]
    assert database.get_assignment(unknown.id).status is Status.NEW


def test_delete(database):
    record = database.create_assignment(sample())
    assert database.delete_assignment(record.id)
    assert database.get_assignment(record.id) is None
    assert not database.assignment_exists(record)
    assert not database.delete_assignment(record.id)


@pytest.mark.parametrize("changes", [
    {"deadline": datetime(2030, 1, 1)}, {"uploaded_at": datetime(2030, 1, 1)},
    {"course": " "}, {"title": ""}, {"status": "INVALID"},
    {"source": "INVALID"}, {"needs_review": "false"}, {"created_at": None},
])
def test_invalid_model(changes):
    with pytest.raises(ValueError):
        sample(**changes)


def test_naive_overdue_clock_rejected(database):
    with pytest.raises(ValueError):
        database.mark_overdue(datetime(2030, 1, 1))
