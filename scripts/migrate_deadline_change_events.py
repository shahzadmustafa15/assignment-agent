from app.config import load_settings
from app.database import Database


db = Database(load_settings().database_path)

with db._connect() as connection:
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")

    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(notification_events)"
        )
    }

    has_previous = "previous_deadline" in columns

    connection.execute("""
        CREATE TABLE notification_events_new (
            event_key TEXT NOT NULL,
            assignment_id INTEGER,
            student_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(
                event_type IN (
                    'NEW_ASSIGNMENT',
                    'DUE_TOMORROW',
                    'DUE_TODAY',
                    'OVERDUE',
                    'DEADLINE_EXTENDED',
                    'DEADLINE_SHORTENED',
                    'LMS_AUTH_EXPIRED'
                )
            ),
            deadline TEXT NOT NULL DEFAULT '',
            previous_deadline TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'pending' CHECK(
                state IN (
                    'pending',
                    'attempted',
                    'sent',
                    'failed',
                    'cancelled'
                )
            ),
            attempted_at TEXT,
            sent_at TEXT,
            PRIMARY KEY(event_key, channel)
        )
    """)

    previous_expr = (
        "COALESCE(previous_deadline, '')"
        if has_previous
        else "''"
    )

    connection.execute(f"""
        INSERT INTO notification_events_new (
            event_key,
            assignment_id,
            student_id,
            channel,
            event_type,
            deadline,
            previous_deadline,
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
            {previous_expr},
            state,
            attempted_at,
            sent_at
        FROM notification_events
    """)

    connection.execute(
        "DROP TABLE notification_events"
    )

    connection.execute(
        """
        ALTER TABLE notification_events_new
        RENAME TO notification_events
        """
    )

    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

print("Deadline-change notification schema installed.")
print("Foreign-key problems:", len(problems))
