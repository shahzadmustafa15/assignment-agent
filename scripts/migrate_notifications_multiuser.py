import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "assignments.db"


def column_exists(connection, table, column):
    return any(
        row[1] == column
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        )
    )


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    try:
        db.execute("BEGIN IMMEDIATE")

        if not column_exists(
            db,
            "notification_events",
            "student_id",
        ):
            db.execute("""
                ALTER TABLE notification_events
                ADD COLUMN student_id TEXT
                REFERENCES students(id)
                ON DELETE CASCADE
            """)

        # Existing assignment-linked events, if any,
        # inherit ownership from assignments.
        db.execute("""
            UPDATE notification_events
            SET student_id = (
                SELECT assignments.student_id
                FROM assignments
                WHERE assignments.id =
                      notification_events.assignment_id
            )
            WHERE student_id IS NULL
              AND assignment_id IS NOT NULL
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            notification_student_state
            ON notification_events(
                student_id,
                state,
                event_type
            )
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    print("Notification multi-user migration complete.")

    columns = [
        row[1]
        for row in db.execute(
            "PRAGMA table_info(notification_events)"
        )
    ]

    print("student_id column:",
          "yes" if "student_id" in columns else "no")

    print(
        "Notification rows:",
        db.execute(
            "SELECT COUNT(*) FROM notification_events"
        ).fetchone()[0],
    )

    db.close()


if __name__ == "__main__":
    main()
