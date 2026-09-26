import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "assignments.db"


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")

        count = db.execute(
            "SELECT COUNT(*) FROM lms_records"
        ).fetchone()[0]

        db.execute("""
            CREATE TABLE lms_records_new (
                student_id TEXT NOT NULL
                    REFERENCES students(id) ON DELETE CASCADE,

                external_id TEXT NOT NULL,

                assignment_id INTEGER NOT NULL
                    REFERENCES assignments(id) ON DELETE CASCADE,

                metadata TEXT NOT NULL,
                observed_at TEXT NOT NULL,

                notify_pending INTEGER NOT NULL DEFAULT 0
                    CHECK(notify_pending IN (0,1)),

                notified_at TEXT,

                PRIMARY KEY(student_id, external_id)
            )
        """)

        db.execute("""
            INSERT INTO lms_records_new (
                student_id,
                external_id,
                assignment_id,
                metadata,
                observed_at,
                notify_pending,
                notified_at
            )
            SELECT
                a.student_id,
                l.external_id,
                l.assignment_id,
                l.metadata,
                l.observed_at,
                l.notify_pending,
                l.notified_at
            FROM lms_records l
            JOIN assignments a
              ON a.id = l.assignment_id
        """)

        copied = db.execute(
            "SELECT COUNT(*) FROM lms_records_new"
        ).fetchone()[0]

        if copied != count:
            raise RuntimeError(
                f"LMS record count mismatch: {count} -> {copied}"
            )

        db.execute("DROP TABLE lms_records")

        db.execute("""
            ALTER TABLE lms_records_new
            RENAME TO lms_records
        """)

        db.execute("""
            CREATE INDEX lms_records_assignment
            ON lms_records(assignment_id)
        """)

        db.execute("""
            CREATE INDEX lms_records_student_pending
            ON lms_records(student_id, notify_pending)
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    print("LMS multi-user migration complete.")

    for row in db.execute("""
        SELECT student_id, COUNT(*) AS total
        FROM lms_records
        GROUP BY student_id
        ORDER BY student_id
    """):
        print(
            row["student_id"],
            "→",
            row["total"],
            "LMS records",
        )

    print(
        "Foreign-key problems:",
        len(db.execute("PRAGMA foreign_key_check").fetchall()),
    )

    db.close()


if __name__ == "__main__":
    main()
