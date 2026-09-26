"""Transactional LMS provenance alongside the existing assignments table."""
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from app.database import Database
from app.models import Source, Status, normalized, utc_now

SCHEMA = '''CREATE TABLE IF NOT EXISTS lms_records (
    student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    metadata TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    notify_pending INTEGER NOT NULL DEFAULT 0 CHECK(notify_pending IN (0,1)),
    notified_at TEXT,
    PRIMARY KEY(student_id, external_id)
)'''


@contextmanager
def readonly_connection(path):
    path = Path(path)
    if not path.exists():
        yield None
        return
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only = ON')
        yield connection
    finally:
        connection.close()


def match_assignment(connection, incoming, *, metadata=None, allow_unknown_course=False):
    if connection is None:
        return None
    has_lms = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lms_records'").fetchone()
    if has_lms:
        row = connection.execute(
            '''SELECT a.*
               FROM assignments a
               JOIN lms_records l ON a.id=l.assignment_id
               WHERE l.student_id=?
                 AND l.external_id=?''',
            (
                incoming.student_id,
                incoming.external_message_id,
            ),
        ).fetchone()
        if row:
            return Database._record(row)
    row = connection.execute(
        "SELECT * FROM assignments "
        "WHERE student_id=? "
        "AND source='lms' "
        "AND external_message_id=?",
        (
            incoming.student_id,
            incoming.external_message_id,
        ),
    ).fetchone()
    if row:
        return Database._record(row)
    # Cross-source matching requires exact normalized course/title plus a known
    # matching deadline. Similar titles and unknown course/deadline never suffice.
    if incoming.deadline is not None and normalized(incoming.course) != 'unknown course':
        rows = connection.execute(
            '''SELECT * FROM assignments
               WHERE student_id=?
                 AND course_key=?
                 AND title_key=?
                 AND deadline_key=?''',
            (
                incoming.student_id,
                normalized(incoming.course),
                normalized(incoming.title),
                incoming.deadline.isoformat(
                    timespec='microseconds'
                ),
            ),
        ).fetchall()
        if len(rows) == 1:
            return Database._record(rows[0])
    if metadata is not None and normalized(incoming.course) != 'unknown course':
        unknown = connection.execute(
            "SELECT * FROM assignments "
            "WHERE student_id=? "
            "AND source='lms' "
            "AND course_key='unknown course' "
            "AND title_key=?",
            (
                incoming.student_id,
                normalized(incoming.title),
            ),
        ).fetchall()
        matches, ambiguous = [], False
        for row in unknown:
            old = Database._record(row)
            entries = connection.execute(
                '''SELECT metadata
                   FROM lms_records
                   WHERE student_id=?
                     AND assignment_id=?''',
                (
                    incoming.student_id,
                    old.id,
                ),
            ).fetchall() if has_lms else []
            old_metadata = [json.loads(entry['metadata']) for entry in entries]
            incoming_course_id = metadata.get('course_context', {}).get('id')
            recorded_ids = {m.get('course_context', {}).get('id') for m in old_metadata} - {None, ''}
            if incoming_course_id and recorded_ids and incoming_course_id not in recorded_ids:
                continue  # Explicit evidence belongs to a different course.
            confirmed = False
            for prior in old_metadata:
                number = normalized(metadata.get('assignment_number', ''))
                term = normalized(metadata.get('semester', ''))
                if (incoming.deadline is not None and old.deadline == incoming.deadline
                        and number and number == normalized(prior.get('assignment_number', ''))
                        and term and term == normalized(prior.get('semester', ''))
                        and (allow_unknown_course or (incoming_course_id and recorded_ids == {incoming_course_id}))):
                    confirmed = True
            if confirmed:
                matches.append(old)
            else:
                ambiguous = True
        if ambiguous or len(matches) > 1:
            raise ValueError('Unknown-course identity is ambiguous; no duplicate or cross-course merge permitted')
        if matches:
            return matches[0]
    # A fallback identifier changes when a deadline changes. Do not turn an
    # ambiguous reschedule into a second assignment or silently merge it.
    conflict = connection.execute(
        "SELECT 1 FROM assignments "
        "WHERE student_id=? "
        "AND course_key=? "
        "AND title_key=? LIMIT 1",
        (
            incoming.student_id,
            normalized(incoming.course),
            normalized(incoming.title),
        ),
    ).fetchone()
    if conflict:
        raise ValueError('Same course/title has uncertain identity or a different deadline; manual review required')
    return None


class LMSStorage:
    def __init__(self, path):
        self.database = Database(path)
        with self.database._connect() as connection:
            connection.execute(SCHEMA)

    def import_record(self, record, *, include_updated=False, allow_unknown_course=False):
        incoming = record.assignment
        now = utc_now()
        with self.database._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = match_assignment(connection, incoming, metadata=record.metadata,
                                       allow_unknown_course=allow_unknown_course)
            if current:
                # Preserve original Gmail identity, email description and local work
                # status. LMS aliases/metadata live in lms_records on the same ID.
                status = current.status
                if incoming.status is Status.SUBMITTED:
                    status = Status.SUBMITTED
                elif status in (Status.NEW, Status.OVERDUE):
                    status = incoming.status
                updated = replace(current, course=(incoming.course if normalized(current.course) == 'unknown course' else current.course),
                    deadline=incoming.deadline or current.deadline,
                    lms_url=incoming.lms_url or current.lms_url,
                    teacher=incoming.teacher or current.teacher,
                    description=current.description or incoming.description,
                    uploaded_at=incoming.uploaded_at or current.uploaded_at,
                    status=status, needs_review=(incoming.needs_review if current.source is Source.LMS
                        else current.needs_review or incoming.needs_review), updated_at=now)
                values = Database._values(updated)
                connection.execute(f"UPDATE assignments SET {', '.join(k + '=?' for k in values)} WHERE id=?",
                                   (*values.values(), current.id))
                assignment_id = current.id
                created = False
            else:
                updated = replace(incoming, created_at=now, updated_at=now)
                values = Database._values(updated)
                cursor = connection.execute(f"INSERT INTO assignments ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                                            tuple(values.values()))
                assignment_id = cursor.lastrowid
                updated = replace(updated, id=assignment_id)
                created = True
            from app.email_notifications import enqueue_assignment

            if created and updated.status is not Status.SUBMITTED:
                enqueue_assignment(
                    connection,
                    updated,
                    'NEW_ASSIGNMENT',
                )

            elif (
                current is not None
                and current.status is not Status.SUBMITTED
                and current.deadline is not None
                and updated.deadline is not None
                and current.deadline != updated.deadline
            ):
                if updated.deadline > current.deadline:
                    deadline_event = 'DEADLINE_EXTENDED'
                else:
                    deadline_event = 'DEADLINE_SHORTENED'

                enqueue_assignment(
                    connection,
                    updated,
                    deadline_event,
                    previous_deadline=current.deadline,
                )
            previous = connection.execute(
                '''SELECT metadata
                   FROM lms_records
                   WHERE student_id=?
                     AND external_id=?''',
                (
                    incoming.student_id,
                    incoming.external_message_id,
                ),
            ).fetchone()
            metadata = json.loads(previous['metadata']) if previous else {}
            prior_metadata = metadata.copy()
            for key, value in record.metadata.items():
                # Review reasons describe this observation, including a cleared list.
                if key == 'review_reasons' or value not in ('', None, [], {}):
                    metadata[key] = value
            connection.execute(
                '''INSERT INTO lms_records (
                    student_id,
                    external_id,
                    assignment_id,
                    metadata,
                    observed_at,
                    notify_pending
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(student_id, external_id)
                DO UPDATE SET
                    metadata=excluded.metadata,
                    observed_at=excluded.observed_at''',
                (
                    incoming.student_id,
                    incoming.external_message_id,
                    assignment_id,
                    json.dumps(metadata, ensure_ascii=True),
                    now.isoformat(),
                    int(
                        created
                        and updated.status is not Status.SUBMITTED
                    ),
                ),
            )
            changed = bool(current and (
                replace(updated, updated_at=current.updated_at) != current
                or metadata != prior_metadata))
            return (updated, created, changed) if include_updated else (updated, created)

    def notify_pending(self, send, student_id=None):
        sent, errors = 0, []

        with self.database._connect() as connection:
            if student_id:
                keys = [
                    r[0]
                    for r in connection.execute(
                        '''SELECT external_id
                           FROM lms_records
                           WHERE student_id=?
                             AND notify_pending=1''',
                        (student_id,),
                    )
                ]
            else:
                keys = [
                    r[0]
                    for r in connection.execute(
                        '''SELECT external_id
                           FROM lms_records
                           WHERE notify_pending=1'''
                    )
                ]
        for key in keys:
            try:
                with self.database._connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if student_id:
                        row = connection.execute(
                            '''SELECT a.*
                               FROM assignments a
                               JOIN lms_records l
                                 ON a.id=l.assignment_id
                               WHERE l.student_id=?
                                 AND l.external_id=?
                                 AND l.notify_pending=1''',
                            (
                                student_id,
                                key,
                            ),
                        ).fetchone()
                    else:
                        row = connection.execute(
                            '''SELECT a.*
                               FROM assignments a
                               JOIN lms_records l
                                 ON a.id=l.assignment_id
                               WHERE l.external_id=?
                                 AND l.notify_pending=1''',
                            (key,),
                        ).fetchone()
                    if row is None:
                        continue
                    assignment = Database._record(row)
                    if assignment.status is not Status.SUBMITTED:
                        send(assignment)
                        sent += 1
                    if student_id:
                        connection.execute(
                            '''UPDATE lms_records
                               SET notify_pending=0,
                                   notified_at=?
                               WHERE student_id=?
                                 AND external_id=?''',
                            (
                                utc_now().isoformat(),
                                student_id,
                                key,
                            ),
                        )
                    else:
                        connection.execute(
                            '''UPDATE lms_records
                               SET notify_pending=0,
                                   notified_at=?
                               WHERE external_id=?''',
                            (
                                utc_now().isoformat(),
                                key,
                            ),
                        )
            except Exception as exc:
                from app.notifier import NotificationError
                if not isinstance(exc, (NotificationError, OSError, sqlite3.Error)):
                    raise
                errors.append('New assignment notification failed; queued for the next manual scan.')
        return sent, errors
