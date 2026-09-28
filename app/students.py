"""Multi-student account registry."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from app.database import Database


@dataclass(frozen=True)
class Student:
    id: str
    name: str
    email: str
    session_path: str
    enabled: bool = True
    auth_status: str = "UNKNOWN"
    last_scan_at: str | None = None


def make_student_id(name: str) -> str:
    value = name.strip().casefold()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def validate_student_id(student_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", student_id):
        raise ValueError("Student ID must contain lowercase letters, digits and single hyphens.")
    return student_id


def student_session_path(student, project_root):
    """Reject shared or redirected paths rather than crossing student sessions."""
    from pathlib import Path
    from app.lms.base import LMSError
    validate_student_id(student.id)
    path = Path(student.session_path)
    if not path.is_absolute():
        path = project_root / path
    expected = project_root / 'credentials' / 'students' / student.id
    if (path.parent != expected or path.name != 'bahria_storage_state.json'
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise LMSError("Student session must use that student's isolated credentials directory.")
    return path


def validate_email(email: str) -> str:
    email = email.strip()

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
        r"\.[A-Za-z]{2,}",
        email,
    ):
        raise ValueError("Invalid email address")

    return email


def add_student(
    database: Database,
    name: str,
    email: str,
    student_id: str | None = None,
) -> Student:

    name = " ".join(name.split())

    if not name:
        raise ValueError("Student name is required")

    email = validate_email(email)

    student_id = student_id or make_student_id(name)

    if not student_id:
        raise ValueError("Could not create student ID")
    validate_student_id(student_id)

    session_path = (
        f"credentials/students/{student_id}/"
        "bahria_storage_state.json"
    )

    now = datetime.now(timezone.utc).isoformat()

    with database._connect() as connection:
        existing = connection.execute(
            "SELECT 1 FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()

        if existing:
            raise ValueError(
                f"Student ID already exists: {student_id}"
            )

        connection.execute(
            """
            INSERT INTO students (
                id,
                name,
                email,
                session_path,
                enabled,
                auth_status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 1, 'UNKNOWN', ?, ?)
            """,
            (
                student_id,
                name,
                email,
                session_path,
                now,
                now,
            ),
        )

    return get_student(database, student_id)


def get_student(
    database: Database,
    student_id: str,
) -> Student | None:

    with database._connect() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                name,
                email,
                session_path,
                enabled,
                auth_status,
                last_scan_at
            FROM students
            WHERE id = ?
            """,
            (student_id,),
        ).fetchone()

    if row is None:
        return None

    return Student(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        session_path=row["session_path"],
        enabled=bool(row["enabled"]),
        auth_status=row["auth_status"],
        last_scan_at=row["last_scan_at"],
    )


def list_students(database: Database) -> list[Student]:

    with database._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                name,
                email,
                session_path,
                enabled,
                auth_status,
                last_scan_at
            FROM students
            ORDER BY name, id
            """
        ).fetchall()

    return [
        Student(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            session_path=row["session_path"],
            enabled=bool(row["enabled"]),
            auth_status=row["auth_status"],
            last_scan_at=row["last_scan_at"],
        )
        for row in rows
    ]


def set_auth_status(
    database: Database,
    student_id: str,
    status: str,
) -> None:

    allowed = {
        "UNKNOWN",
        "ACTIVE",
        "EXPIRED",
        "LOGIN_REQUIRED",
    }

    if status not in allowed:
        raise ValueError("Invalid authentication status")

    now = datetime.now(timezone.utc).isoformat()

    with database._connect() as connection:
        result = connection.execute(
            """
            UPDATE students
            SET auth_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                now,
                student_id,
            ),
        )

        if result.rowcount == 0:
            raise ValueError(
                f"Unknown student: {student_id}"
            )
