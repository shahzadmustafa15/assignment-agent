from app.config import load_settings
from app.database import Database

db = Database(load_settings().database_path)

with db._connect() as connection:
    connection.execute("BEGIN IMMEDIATE")

    schema = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='table'
          AND name='notification_events'
        """
    ).fetchone()

    if schema is None:
        raise RuntimeError(
            "notification_events table does not exist."
        )

    if "'OVERDUE'" in schema["sql"]:
        print("OVERDUE notification schema already installed.")
    else:
        connection.execute(
            """
            ALTER TABLE notification_events
            RENAME TO notification_events_before_overdue
            """
        )

        connection.execute(
            """
            CREATE TABLE notification_events (
                event_key TEXT NOT NULL,
                assignment_id INTEGER
                    REFERENCES assignments(id)
                    ON DELETE CASCADE,
                student_id TEXT
                    REFERENCES students(id)
                    ON DELETE CASCADE,
                channel TEXT NOT NULL
                    CHECK(channel = 'gmail'),
                event_type TEXT NOT NULL
                    CHECK(event_type IN (
                        'NEW_ASSIGNMENT',
                        'DUE_TOMORROW',
                        'DUE_TODAY',
                        'OVERDUE',
                        'LMS_AUTH_EXPIRED'
                    )),
                deadline TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN (
                        'pending',
                        'attempted',
                        'sent',
                        'failed',
                        'cancelled'
                    )),
                attempted_at TEXT,
                sent_at TEXT,
                PRIMARY KEY(event_key, channel)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO notification_events (
                event_key,
                assignment_id,
                student_id,
                channel,
                event_type,
                deadline,
                state,
                attempted_at,
                sent_at
            )
            SELECT
                event_key,
                assignment_id,
                student_id,
                channel,
                event_type,
                deadline,
                state,
                attempted_at,
                sent_at
            FROM notification_events_before_overdue
            """
        )

        connection.execute(
            "DROP TABLE notification_events_before_overdue"
        )

        print("OVERDUE notification schema installed.")

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("Foreign-key problems:", len(problems))
