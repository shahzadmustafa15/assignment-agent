"""Independent Gmail event delivery with durable, at-most-once send attempts.

No network at import time. Blank ALERT_EMAIL disables the channel. Desktop
history stays in its existing tables; this table tracks the email channel.
"""
import base64
from email.message import EmailMessage
import logging
import os
import re
import sqlite3

from app.models import LOCAL_TIMEZONE, Status, utc_now

LOG = logging.getLogger(__name__)
KINDS = {
    'NEW_ASSIGNMENT',
    'DUE_TOMORROW',
    'DUE_TODAY',
    'OVERDUE',
    'DEADLINE_EXTENDED',
    'DEADLINE_SHORTENED',
    'LMS_AUTH_EXPIRED',
}


def recipient():
    return os.environ.get('ALERT_EMAIL', '').strip()


def student_recipient(connection, student_id):
    if not student_id:
        raise ValueError("Notification event has no student_id.")

    row = connection.execute(
        "SELECT email FROM students WHERE id=? AND enabled=1",
        (student_id,),
    ).fetchone()

    if row is None:
        raise ValueError(
            f"Enabled student not found: {student_id}"
        )

    address = row["email"].strip()

    if not address:
        raise ValueError(
            f"Student has no notification email: {student_id}"
        )

    return address


def initialize_events(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS notification_events (
        event_key TEXT NOT NULL,
        assignment_id INTEGER REFERENCES assignments(id) ON DELETE CASCADE,
        student_id TEXT REFERENCES students(id) ON DELETE CASCADE,
        channel TEXT NOT NULL CHECK(channel = 'gmail'),
        event_type TEXT NOT NULL CHECK(event_type IN
            ('NEW_ASSIGNMENT','DUE_TOMORROW','DUE_TODAY','OVERDUE','DEADLINE_EXTENDED','DEADLINE_SHORTENED','LMS_AUTH_EXPIRED')),
        deadline TEXT NOT NULL DEFAULT '',
        previous_deadline TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN
            ('pending','attempted','sent','failed','cancelled')),
        attempted_at TEXT,
        sent_at TEXT,
        PRIMARY KEY(event_key, channel)
    )''')


def enqueue_assignment(
    connection,
    assignment,
    kind,
    *,
    previous_deadline=None,
):
    if assignment.status is Status.SUBMITTED:
        return

    if kind not in KINDS - {'LMS_AUTH_EXPIRED'}:
        raise ValueError(
            'Unsupported assignment email event'
        )

    deadline = (
        assignment.deadline.isoformat(
            timespec='microseconds'
        )
        if assignment.deadline
        else ''
    )

    old_deadline = (
        previous_deadline.isoformat(
            timespec='microseconds'
        )
        if previous_deadline
        else ''
    )

    if kind == 'NEW_ASSIGNMENT':
        version = ''

    elif kind in {
        'DEADLINE_EXTENDED',
        'DEADLINE_SHORTENED',
    }:
        version = f'{old_deadline}->{deadline}'

    else:
        version = deadline

    key = (
        f'{assignment.student_id}:'
        f'{assignment.id}:'
        f'{kind}:'
        f'{version}'
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO notification_events (
            event_key,
            assignment_id,
            student_id,
            channel,
            event_type,
            deadline,
            previous_deadline,
            created_at
        )
        VALUES (?, ?, ?, 'gmail', ?, ?, ?, ?)
        """,
        (
            key,
            assignment.id,
            assignment.student_id,
            kind,
            deadline,
            old_deadline,
            utc_now().isoformat(),
        ),
    )


def email_content(kind, assignment=None):
    if kind == 'LMS_AUTH_EXPIRED':
        return ('Bahria LMS Login Required',
                'Your Assignment Agent could not access Bahria LMS because the saved session\n'
                'expired.\n\nRun:\npython -m app.main lms-auth')
    label = {
        'NEW_ASSIGNMENT': 'New Bahria Assignment',
        'DUE_TOMORROW': 'Assignment Due Tomorrow',
        'DUE_TODAY': 'Assignment Due Today',
        'OVERDUE': 'Overdue Assignment',
        'DEADLINE_EXTENDED': 'Assignment Deadline Extended',
        'DEADLINE_SHORTENED': 'Assignment Deadline Shortened',
    }[kind]
    suffix = assignment.course if kind == 'NEW_ASSIGNMENT' else assignment.title
    subject = ' '.join(f'{label} — {suffix}'.splitlines())
    deadline = (assignment.deadline.astimezone(LOCAL_TIMEZONE).strftime('%B %d, %Y, %I:%M %p (Asia/Karachi)')
                if assignment.deadline else 'Unknown — review required')
    status = 'Submitted' if assignment.status is Status.SUBMITTED else 'Not Submitted'
    prefix = 'New assignment detected.\n\n' if kind == 'NEW_ASSIGNMENT' else ''
    body = (f'{prefix}Course: {assignment.course}\nAssignment: {assignment.title}\n'
            f'Deadline: {deadline}\nSubmission status: {status}\n\nCheck Bahria LMS.')
    if assignment.lms_url:
        from app.lms.parser import public_link
        link = public_link(assignment.lms_url, assignment.lms_url)
        if link:
            body += '\n' + link
    return subject, body


def send_email(subject, body, address=None, html_body=None):
    """One Gmail API send; no retry of a possibly accepted POST."""
    from app.config import load_gmail_settings
    from app.gmail_auth import GmailError, gmail_service, api_execute
    address = (address or recipient()).strip()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", address):
        raise GmailError('Set ALERT_EMAIL to one valid recipient address.')
    service = None
    try:
        message = EmailMessage()
        message['To'] = address
        message['Subject'] = subject
        message.set_content(body)

        if html_body:
            message.add_alternative(
                html_body,
                subtype="html",
            )

        service = gmail_service(load_gmail_settings())  # Never interactive.
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
        response = api_execute(service.users().messages().send(userId='me', body={'raw': raw}), max_retries=0)
        if not isinstance(response, dict) or not response.get('id'):
            raise GmailError(
                'Gmail send result was uncertain; no automatic resend.'
            )

        return response['id']
    except GmailError:
        raise
    except Exception as exc:
        raise GmailError('Gmail email delivery failed; no automatic resend.') from exc
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                LOG.warning('Gmail email client cleanup failed (details redacted).')


def send_test_email():
    send_email('Assignment Agent Test', 'Your Assignment Agent Gmail notifications are working.')


def deliver_pending(database, now=None, *, event_types=None):
    """Claim durably before POST, preventing concurrent/crash/timeout resends.

    A failed/uncertain attempt is kept for review, never automatically retried.
    Other channels retain their own delivery and retry behavior.
    """
    from app.database import Database
    from app.scheduler import reminder_window
    now = now or utc_now()
    event_types = set(event_types or KINDS)
    try:
        with database._connect() as connection:
            keys = [row[0] for row in connection.execute(
                "SELECT event_key FROM notification_events WHERE state='pending' AND channel='gmail'")]
        for key in keys:
            with database._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                event = connection.execute("SELECT * FROM notification_events WHERE event_key=? AND channel='gmail' AND state='pending'", (key,)).fetchone()
                if event is None or event['event_type'] not in event_types:
                    continue
                assignment = None
                if event['assignment_id'] is not None:
                    assignment = Database._record(connection.execute('SELECT * FROM assignments WHERE id=?', (event['assignment_id'],)).fetchone())
                    valid = assignment is not None and assignment.status is not Status.SUBMITTED
                    if (
                        valid
                        and event['event_type']
                        not in {
                            'NEW_ASSIGNMENT',
                            'OVERDUE',
                            'DEADLINE_EXTENDED',
                            'DEADLINE_SHORTENED',
                        }
                    ):
                        window = reminder_window(
                            assignment,
                            now,
                        )

                        valid = bool(
                            window
                            and window[0] == event['event_type']
                            and assignment.deadline.isoformat(
                                timespec='microseconds'
                            ) == event['deadline']
                        )

                    elif (
                        valid
                        and event['event_type'] in {
                            'DEADLINE_EXTENDED',
                            'DEADLINE_SHORTENED',
                        }
                    ):
                        current_deadline = (
                            assignment.deadline.isoformat(
                                timespec='microseconds'
                            )
                            if assignment.deadline
                            else ''
                        )

                        valid = bool(
                            event['previous_deadline']
                            and event['deadline']
                            and event['previous_deadline']
                            != event['deadline']
                            and current_deadline
                            == event['deadline']
                        )

                    elif (
                        valid
                        and event['event_type'] == 'OVERDUE'
                    ):
                        from datetime import timedelta

                        local_today = now.astimezone(
                            LOCAL_TIMEZONE
                        ).date()

                        due_date = (
                            assignment.deadline.astimezone(
                                LOCAL_TIMEZONE
                            ).date()
                            if assignment.deadline
                            else None
                        )

                        valid = bool(
                            due_date
                            and due_date
                            == local_today - timedelta(days=1)
                            and assignment.deadline.isoformat(
                                timespec='microseconds'
                            ) == event['deadline']
                        )
                    if not valid:
                        connection.execute("UPDATE notification_events SET state='cancelled' WHERE event_key=? AND channel='gmail'", (key,))
                        continue
                address = student_recipient(
                    connection,
                    event['student_id'],
                )

                html_body = None

                if (
                    assignment is not None
                    and event['event_type'] in {
                        'DEADLINE_EXTENDED',
                        'DEADLINE_SHORTENED',
                    }
                ):
                    from app.email_templates import (
                        build_deadline_change_event,
                    )

                    student_row = connection.execute(
                        "SELECT name FROM students WHERE id=?",
                        (event['student_id'],),
                    ).fetchone()

                    student_name = (
                        student_row["name"]
                        if student_row is not None
                        else "Student"
                    )

                    subject, body, html_body = (
                        build_deadline_change_event(
                            student_name,
                            assignment,
                            event['event_type'],
                            event['previous_deadline'],
                        )
                    )

                elif (
                    assignment is not None
                    and event['event_type']
                    in {
                        'NEW_ASSIGNMENT',
                        'DUE_TOMORROW',
                        'DUE_TODAY',
                        'OVERDUE',
                    }
                ):
                    from app.email_templates import (
                        build_assignment_event,
                    )

                    student_row = connection.execute(
                        "SELECT name FROM students WHERE id=?",
                        (event['student_id'],),
                    ).fetchone()

                    student_name = (
                        student_row["name"]
                        if student_row is not None
                        else "Student"
                    )

                    subject, body, html_body = (
                        build_assignment_event(
                            student_name,
                            assignment,
                            event['event_type'],
                        )
                    )
                else:
                    subject, body = email_content(
                        event['event_type'],
                        assignment,
                    )

                connection.execute(
                    "UPDATE notification_events "
                    "SET state='attempted', "
                    "attempted_at=?, "
                    "recipient_email=?, "
                    "error_type=NULL, "
                    "error_message=NULL "
                    "WHERE event_key=? AND channel='gmail'",
                    (
                        now.isoformat(),
                        address,
                        key,
                    ),
                )
            # This commit is intentionally before sending. Never roll it back on a
            # network error: Gmail may already have accepted the message.
            gmail_message_id = None
            error_type = None
            error_message = None

            try:
                gmail_message_id = send_email(
                    subject,
                    body,
                    address,
                    html_body=html_body,
                )

            except Exception as exc:
                LOG.warning(
                    'Gmail notification failed; event retained '
                    'without automatic resend. Details redacted.'
                )

                state = 'failed'
                sent = None
                error_type = type(exc).__name__

                # Do not store raw provider errors because they may
                # contain sensitive account or authentication data.
                error_message = (
                    'Gmail delivery failed. '
                    'Review application logs and Gmail authorization.'
                )

            else:
                state = 'sent'
                sent = utc_now().isoformat()

            with database._connect() as connection:
                connection.execute(
                    '''
                    UPDATE notification_events
                    SET state=?,
                        sent_at=?,
                        gmail_message_id=?,
                        error_type=?,
                        error_message=?
                    WHERE event_key=?
                      AND channel='gmail'
                    ''',
                    (
                        state,
                        sent,
                        gmail_message_id,
                        error_type,
                        error_message,
                        key,
                    ),
                )
    except (OSError, sqlite3.Error, ValueError):
        LOG.warning('Gmail notification event storage failed (details redacted); desktop delivery remains independent.')


def notify_due(database, now):
    from app.database import Database
    from app.scheduler import reminder_window
    try:
        with database._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            has_lms = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lms_records'").fetchone()
            for row in connection.execute('SELECT * FROM assignments'):
                assignment = Database._record(row)
                is_lms = assignment.source.value == 'lms' or (has_lms and connection.execute('SELECT 1 FROM lms_records WHERE assignment_id=?', (assignment.id,)).fetchone())
                window = reminder_window(assignment, now)
                if is_lms and window:
                    enqueue_assignment(
                        connection,
                        assignment,
                        window[0],
                    )

                if (
                    is_lms
                    and assignment.status is not Status.SUBMITTED
                    and assignment.deadline is not None
                ):
                    from datetime import timedelta

                    local_today = now.astimezone(
                        LOCAL_TIMEZONE
                    ).date()

                    due_date = assignment.deadline.astimezone(
                        LOCAL_TIMEZONE
                    ).date()

                    if due_date == local_today - timedelta(days=1):
                        enqueue_assignment(
                            connection,
                            assignment,
                            'OVERDUE',
                        )
        deliver_pending(
            database,
            now,
            event_types={
                'DUE_TOMORROW',
                'DUE_TODAY',
                'OVERDUE',
            },
        )
    except (OSError, sqlite3.Error, ValueError):
        LOG.warning('Gmail reminder event storage failed (details redacted).')


def notify_auth_expired(
    database_path,
    fingerprint,
    now,
    *,
    student_id=None,
):
    # Legacy single-user background commands have no student ownership.
    # Their desktop notification remains available, but multi-user Gmail
    # delivery requires an explicit student.
    if not student_id:
        return

    from app.database import Database

    try:
        database = Database(database_path)

        with database._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')

            latest = connection.execute(
                """
                SELECT attempted_at
                FROM notification_events
                WHERE event_type='LMS_AUTH_EXPIRED'
                  AND student_id=?
                  AND attempted_at IS NOT NULL
                ORDER BY attempted_at DESC
                LIMIT 1
                """,
                (student_id,),
            ).fetchone()

            if latest:
                from datetime import datetime

                if (
                    now
                    - datetime.fromisoformat(
                        latest[0]
                    ).timestamp()
                    < 24 * 60 * 60
                ):
                    return

            key = (
                f'{student_id}:auth:'
                f'{fingerprint}:{now}'
            )

            connection.execute(
                """
                INSERT OR IGNORE INTO notification_events (
                    event_key,
                    student_id,
                    channel,
                    event_type
                )
                VALUES (?, ?, 'gmail', 'LMS_AUTH_EXPIRED')
                """,
                (
                    key,
                    student_id,
                ),
            )

        from datetime import datetime, timezone

        deliver_pending(
            database,
            datetime.fromtimestamp(
                now,
                timezone.utc,
            ),
            event_types={
                'LMS_AUTH_EXPIRED'
            },
        )

    except (
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        LOG.warning(
            'Gmail authentication alert storage failed '
            '(details redacted).'
        )
