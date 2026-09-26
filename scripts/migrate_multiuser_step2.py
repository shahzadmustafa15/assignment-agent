import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "assignments.db"


def main():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        # Table rebuild is required because SQLite cannot directly remove
        # the old UNIQUE(course_key, title_key, deadline_key) constraint.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")

        before = connection.execute(
            "SELECT COUNT(*) FROM assignments"
        ).fetchone()[0]

        connection.execute("""
            CREATE TABLE assignments_new (
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
                source TEXT NOT NULL
                    CHECK(source IN ('gmail', 'lms', 'manual')),
                external_message_id TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'NEW',
                    'REVIEWED',
                    'IN_PROGRESS',
                    'READY_TO_SUBMIT',
                    'SUBMITTED',
                    'OVERDUE'
                )),
                needs_review INTEGER NOT NULL
                    CHECK(needs_review IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                course_key TEXT NOT NULL,
                title_key TEXT NOT NULL,
                deadline_key TEXT NOT NULL,

                UNIQUE(
                    student_id,
                    course_key,
                    title_key,
                    deadline_key
                )
            )
        """)

        connection.execute("""
            INSERT INTO assignments_new (
                id,
                student_id,
                course,
                title,
                teacher,
                description,
                uploaded_at,
                deadline,
                lms_url,
                source,
                external_message_id,
                status,
                needs_review,
                created_at,
                updated_at,
                course_key,
                title_key,
                deadline_key
            )
            SELECT
                id,
                student_id,
                course,
                title,
                teacher,
                description,
                uploaded_at,
                deadline,
                lms_url,
                source,
                external_message_id,
                status,
                needs_review,
                created_at,
                updated_at,
                course_key,
                title_key,
                deadline_key
            FROM assignments
        """)

        copied = connection.execute(
            "SELECT COUNT(*) FROM assignments_new"
        ).fetchone()[0]

        if copied != before:
            raise RuntimeError(
                f"Assignment count mismatch: before={before}, copied={copied}"
            )

        connection.execute("DROP TABLE assignments")
        connection.execute(
            "ALTER TABLE assignments_new RENAME TO assignments"
        )

        connection.execute("""
            CREATE UNIQUE INDEX assignment_external_id
            ON assignments(
                student_id,
                source,
                external_message_id
            )
            WHERE external_message_id IS NOT NULL
        """)

        connection.execute("""
            CREATE INDEX assignment_status_deadline
            ON assignments(status, deadline)
        """)

        connection.execute("""
            CREATE INDEX assignment_student_id
            ON assignments(student_id)
        """)

        connection.execute("""
            CREATE INDEX assignment_student_status_deadline
            ON assignments(student_id, status, deadline)
        """)

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    # Verify with foreign keys enabled.
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    print("\nStep 2 migration successful.\n")

    print("Assignments:")
    for row in connection.execute("""
        SELECT student_id, COUNT(*) AS count
        FROM assignments
        GROUP BY student_id
    """):
        print(f"  {row['student_id']}: {row['count']}")

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print(f"\nForeign-key problems: {len(problems)}")

    schema = connection.execute("""
        SELECT sql
        FROM sqlite_master
        WHERE type='table'
          AND name='assignments'
    """).fetchone()[0]

    print("\nAssignments schema:")
    print(schema)

    connection.close()


if __name__ == "__main__":
    main()

