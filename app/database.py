"""SQLite repository. Connections are short-lived and writes are transactional."""

import sqlite3
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from app.models import Assignment, LOCAL_TIMEZONE, Source, Status, aware_utc, normalized, utc_now


class DuplicateAssignmentError(ValueError):
    """An assignment matches an existing identifier or natural key."""


class AssignmentNotFoundError(LookupError):
    """The requested assignment no longer exists."""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS students (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    session_path TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                        CHECK(enabled IN (0,1)),
                    auth_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                    last_scan_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY,
                    student_id TEXT NOT NULL
                        REFERENCES students(id) ON DELETE CASCADE,
                    course TEXT NOT NULL,
                    title TEXT NOT NULL,
                    teacher TEXT NOT NULL,
                    description TEXT NOT NULL,
                    uploaded_at TEXT,
                    deadline TEXT,
                    lms_url TEXT,
                    source TEXT NOT NULL CHECK(source IN ('gmail', 'lms', 'manual')),
                    external_message_id TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('NEW','REVIEWED','IN_PROGRESS','READY_TO_SUBMIT','SUBMITTED','OVERDUE')),
                    needs_review INTEGER NOT NULL CHECK(needs_review IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    course_key TEXT NOT NULL,
                    title_key TEXT NOT NULL,
                    deadline_key TEXT NOT NULL,
                    UNIQUE(student_id, course_key, title_key, deadline_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS assignment_external_id
                    ON assignments(student_id, source, external_message_id)
                    WHERE external_message_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS gmail_messages (
                    message_id TEXT PRIMARY KEY,
                    assignment_id INTEGER REFERENCES assignments(id) ON DELETE SET NULL,
                    subject TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    received_at TEXT,
                    outcome TEXT NOT NULL CHECK(outcome IN ('created', 'duplicate', 'ignored')),
                    processed_at TEXT NOT NULL,
                    notified_at TEXT
                );
                CREATE INDEX IF NOT EXISTS assignment_status_deadline
                    ON assignments(status, deadline);

                CREATE INDEX IF NOT EXISTS assignment_student_id
                    ON assignments(student_id);

                CREATE INDEX IF NOT EXISTS assignment_student_status_deadline
                    ON assignments(student_id, status, deadline);
            """)

        from app.email_notifications import initialize_events
        with self._connect() as connection:
            initialize_events(connection)
        self._initialize_reminder_events()

    def _initialize_reminder_events(self) -> None:
        """Atomically migrate old delivery history to the two calendar-day types."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reminder_events'"
            ).fetchone()
            if schema is not None and "'DUE_TODAY'" in schema["sql"]:
                return
            if schema is not None:
                connection.execute("ALTER TABLE reminder_events RENAME TO reminder_events_legacy")
            connection.execute("""
                CREATE TABLE reminder_events (
                    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
                    deadline TEXT NOT NULL,
                    reminder_type TEXT NOT NULL CHECK(reminder_type IN ('DUE_TOMORROW', 'DUE_TODAY')),
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY(assignment_id, deadline, reminder_type)
                )
            """)
            if schema is None:
                return
            # Preserve all old events in the archive. A delivery already made on
            # either eligible local date counts, regardless of its former type.
            for event in connection.execute("SELECT * FROM reminder_events_legacy ORDER BY sent_at"):
                due = aware_utc(datetime.fromisoformat(event["deadline"]))
                sent = aware_utc(datetime.fromisoformat(event["sent_at"]))
                days_before = due.astimezone(LOCAL_TIMEZONE).date() - sent.astimezone(LOCAL_TIMEZONE).date()
                kind = {timedelta(days=1): "DUE_TOMORROW", timedelta(0): "DUE_TODAY"}.get(days_before)
                if kind is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO reminder_events VALUES (?, ?, ?, ?)",
                        (event["assignment_id"], due.isoformat(timespec="microseconds"), kind,
                         sent.isoformat(timespec="microseconds")))

    @staticmethod
    def _values(assignment: Assignment) -> dict:
        result = {}
        for field in fields(assignment):
            if field.name == "id":
                continue
            value = getattr(assignment, field.name)
            if isinstance(value, datetime):
                value = value.isoformat(timespec="microseconds")
            elif isinstance(value, (Source, Status)):
                value = value.value
            elif isinstance(value, bool):
                value = int(value)
            result[field.name] = value
        result.update(course_key=normalized(assignment.course),
                      title_key=normalized(assignment.title),
                      deadline_key=result["deadline"] or "")
        return result

    @staticmethod
    def _record(row) -> Assignment | None:
        if row is None:
            return None
        values = {field.name: row[field.name] for field in fields(Assignment)}
        for name in ("uploaded_at", "deadline", "created_at", "updated_at"):
            if values[name] is not None:
                values[name] = datetime.fromisoformat(values[name])
        values["needs_review"] = bool(values["needs_review"])
        return Assignment(**values)

    def assignment_exists(self, assignment: Assignment) -> bool:
        values = self._values(assignment)
        with self._connect() as connection:
            return connection.execute("""
                SELECT 1 FROM assignments WHERE
                    (student_id = :student_id
                        AND source = :source
                        AND external_message_id = :external_message_id)
                    OR (student_id = :student_id
                        AND course_key = :course_key
                        AND title_key = :title_key
                        AND deadline_key = :deadline_key) LIMIT 1
            """, values).fetchone() is not None

    def create_assignment(self, assignment: Assignment) -> Assignment:
        if assignment.id is not None:
            raise ValueError("New assignments must not already have an id")
        now = utc_now()
        assignment = replace(assignment, created_at=now, updated_at=now)
        values = self._values(assignment)
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    f"INSERT INTO assignments ({', '.join(values)}) "
                    f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
            except sqlite3.IntegrityError as exc:
                if str(exc).startswith("UNIQUE constraint failed:"):
                    raise DuplicateAssignmentError("Assignment already exists") from exc
                raise
            return replace(assignment, id=cursor.lastrowid)

    def get_assignment(self, assignment_id: int) -> Assignment | None:
        with self._connect() as connection:
            return self._record(connection.execute(
                "SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone())

    def list_assignments(
        self,
        status: Status | str | None = None,
        student_id: str | None = None,
    ) -> list[Assignment]:
        with self._connect() as connection:
            query = "SELECT * FROM assignments"
            conditions = []
            parameters = []

            if status is not None:
                conditions.append("status = ?")
                parameters.append(Status(status).value)

            if student_id is not None:
                conditions.append("student_id = ?")
                parameters.append(student_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY deadline IS NULL, deadline, id"

            return [
                self._record(row)
                for row in connection.execute(query, tuple(parameters))
            ]

    def list_by_status(
        self,
        status: Status | str,
        student_id: str | None = None,
    ) -> list[Assignment]:
        return self.list_assignments(status, student_id)

    def update_assignment(self, assignment_id: int, **changes) -> Assignment:
        allowed = {field.name for field in fields(Assignment)} - {"id", "created_at", "updated_at"}
        if set(changes) - allowed:
            raise ValueError("Unknown or read-only assignment fields")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._record(connection.execute(
                "SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone())
            if current is None:
                raise AssignmentNotFoundError(f"Assignment {assignment_id} not found")
            updated = replace(current, **changes, updated_at=utc_now())
            values = self._values(updated)
            try:
                connection.execute(
                    f"UPDATE assignments SET {', '.join(name + ' = ?' for name in values)} WHERE id = ?",
                    (*values.values(), assignment_id))
            except sqlite3.IntegrityError as exc:
                if str(exc).startswith("UNIQUE constraint failed:"):
                    raise DuplicateAssignmentError("Update would duplicate an assignment") from exc
                raise
            return updated

    def mark_submitted(self, assignment_id: int) -> Assignment:
        """Record a submission locally; never perform an actual submission."""
        return self.update_assignment(assignment_id, status=Status.SUBMITTED)

    def delete_assignment(self, assignment_id: int) -> bool:
        with self._connect() as connection:
            return connection.execute("DELETE FROM assignments WHERE id = ?",
                                      (assignment_id,)).rowcount > 0

    def mark_overdue(self, now: datetime | None = None) -> int:
        timestamp = aware_utc(now if now is not None else utc_now()).isoformat(timespec="microseconds")
        with self._connect() as connection:
            return connection.execute("""
                UPDATE assignments SET status = 'OVERDUE', updated_at = ?
                WHERE deadline < ? AND status NOT IN ('SUBMITTED', 'OVERDUE')
            """, (timestamp, timestamp)).rowcount

    def upcoming(self, now: datetime | None = None) -> list[Assignment]:
        timestamp = aware_utc(now if now is not None else utc_now()).isoformat(timespec="microseconds")
        with self._connect() as connection:
            return [self._record(row) for row in connection.execute("""
                SELECT * FROM assignments WHERE deadline >= ? AND status != 'SUBMITTED'
                ORDER BY deadline, id
            """, (timestamp,))]


    def send_reminder_once(self, assignment: Assignment, reminder_type: str,
                           send: Callable[[Assignment], None], now: datetime) -> bool:
        """Serialize check/send/record across processes; failed sends roll back.

        The bounded notification call runs under a write lock. A process crash
        after delivery but before commit can still cause a retry (see README).
        """
        if reminder_type not in {"DUE_TOMORROW", "DUE_TODAY"}:
            raise ValueError("Unknown reminder type")
        timestamp = aware_utc(now).isoformat(timespec="microseconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._record(connection.execute(
                "SELECT * FROM assignments WHERE id = ?", (assignment.id,)).fetchone())
            if (current is None or current.status is Status.SUBMITTED or
                    current.deadline is None or current.deadline != assignment.deadline):
                return False
            key = (current.id, current.deadline.isoformat(timespec="microseconds"), reminder_type)
            if connection.execute(
                "SELECT 1 FROM reminder_events WHERE assignment_id = ? AND deadline = ? "
                "AND reminder_type = ?", key).fetchone():
                return False
            send(current)
            connection.execute(
                "INSERT INTO reminder_events (assignment_id, deadline, reminder_type, sent_at) "
                "VALUES (?, ?, ?, ?)", (*key, timestamp))
            return True

    def list_reminder_events(self, assignment_id: int) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM reminder_events WHERE assignment_id = ? ORDER BY sent_at, reminder_type",
                (assignment_id,))]


    def gmail_message_processed(self, message_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM gmail_messages WHERE message_id = ? UNION ALL "
                "SELECT 1 FROM assignments WHERE source = 'gmail' AND external_message_id = ? LIMIT 1",
                (message_id, message_id)).fetchone() is not None

    def record_gmail_message(self, message_id: str, subject: str, sender: str,
                             received_at: datetime | None, assignment: Assignment | None) -> str:
        """Atomically store assignment plus processed marker; concurrent scans converge."""
        if not message_id or (assignment is not None and
                (assignment.source is not Source.GMAIL or assignment.external_message_id != message_id)):
            raise ValueError("Gmail assignment must match its message ID and source")
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM gmail_messages WHERE message_id = ?", (message_id,)).fetchone():
                return "duplicate"
            assignment_id = None
            outcome = "ignored"
            if assignment is not None:
                values = self._values(replace(assignment, created_at=now, updated_at=now))
                existing = connection.execute("""
                    SELECT id FROM assignments WHERE
                        (student_id = :student_id
                            AND source = :source
                            AND external_message_id = :external_message_id)
                        OR (student_id = :student_id
                            AND course_key = :course_key
                            AND title_key = :title_key
                            AND deadline_key = :deadline_key)
                    ORDER BY id LIMIT 1
                """, values).fetchone()
                if existing:
                    assignment_id = existing["id"]
                    outcome = "duplicate"
                else:
                    cursor = connection.execute(
                        f"INSERT INTO assignments ({', '.join(values)}) "
                        f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
                    assignment_id = cursor.lastrowid
                    outcome = "created"
            connection.execute("""
                INSERT INTO gmail_messages
                (message_id, assignment_id, subject, sender, received_at, outcome, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (message_id, assignment_id, subject if assignment else "", sender if assignment else "",
                  aware_utc(received_at).isoformat() if received_at else None, outcome, now.isoformat()))
            return outcome

    def pending_gmail_notifications(self) -> list[str]:
        with self._connect() as connection:
            return [row[0] for row in connection.execute("""
                SELECT message_id FROM gmail_messages JOIN assignments ON assignments.id = assignment_id
                WHERE outcome = 'created' AND notified_at IS NULL AND status != 'SUBMITTED'
                ORDER BY processed_at
            """)]

    def notify_gmail_once(self, message_id: str, send: Callable[[Assignment], None]) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT assignments.* FROM assignments JOIN gmail_messages ON assignment_id = assignments.id
                WHERE message_id = ? AND outcome = 'created' AND notified_at IS NULL AND status != 'SUBMITTED'
            """, (message_id,)).fetchone()
            if row is None:
                return False
            send(self._record(row))
            connection.execute("UPDATE gmail_messages SET notified_at = ? WHERE message_id = ?",
                               (utc_now().isoformat(), message_id))
            return True
