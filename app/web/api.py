from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from app.config import load_settings
from app.database import Database
from app.models import LOCAL_TIMEZONE, Status


router = APIRouter(prefix="/api")

db = Database(load_settings().database_path)


@router.get("/students")
def get_students():
    with db._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                name,
                email,
                enabled
            FROM students
            ORDER BY name
            """
        ).fetchall()

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]


@router.get("/dashboard/{student_id}")
def get_student_dashboard(student_id: str):
    assignments = db.list_assignments(
        student_id=student_id
    )

    if assignments is None:
        raise HTTPException(
            status_code=404,
            detail="Student not found",
        )

    now = datetime.now(LOCAL_TIMEZONE)
    today = now.date()

    total = len(assignments)

    submitted = [
        a
        for a in assignments
        if a.status is Status.SUBMITTED
    ]

    overdue = [
        a
        for a in assignments
        if (
            a.status is not Status.SUBMITTED
            and a.deadline is not None
            and a.deadline.astimezone(
                LOCAL_TIMEZONE
            ).date() < today
        )
    ]

    due_today = [
        a
        for a in assignments
        if (
            a.status is not Status.SUBMITTED
            and a.deadline is not None
            and a.deadline.astimezone(
                LOCAL_TIMEZONE
            ).date() == today
        )
    ]

    due_soon = [
        a
        for a in assignments
        if (
            a.status is not Status.SUBMITTED
            and a.deadline is not None
            and today
            < a.deadline.astimezone(
                LOCAL_TIMEZONE
            ).date()
            <= today + timedelta(days=3)
        )
    ]

    pending = [
        a
        for a in assignments
        if a.status is not Status.SUBMITTED
    ]

    courses = {}

    for assignment in assignments:
        course = assignment.course or "Unknown course"

        if course not in courses:
            courses[course] = {
                "course": course,
                "total": 0,
                "submitted": 0,
                "pending": 0,
                "overdue": 0,
            }

        courses[course]["total"] += 1

        if assignment.status is Status.SUBMITTED:
            courses[course]["submitted"] += 1
        else:
            courses[course]["pending"] += 1

            if (
                assignment.deadline is not None
                and assignment.deadline.astimezone(
                    LOCAL_TIMEZONE
                ).date() < today
            ):
                courses[course]["overdue"] += 1

    upcoming = sorted(
        [
            a
            for a in assignments
            if (
                a.status is not Status.SUBMITTED
                and a.deadline is not None
                and a.deadline.astimezone(
                    LOCAL_TIMEZONE
                ).date() >= today
            )
        ],
        key=lambda a: a.deadline,
    )[:10]

    return {
        "student_id": student_id,
        "counts": {
            "total": total,
            "submitted": len(submitted),
            "pending": len(pending),
            "overdue": len(overdue),
            "due_today": len(due_today),
            "due_soon": len(due_soon),
        },
        "courses": list(courses.values()),
        "upcoming": [
            {
                "id": a.id,
                "course": a.course,
                "title": a.title,
                "deadline": (
                    a.deadline.astimezone(
                        LOCAL_TIMEZONE
                    ).isoformat()
                    if a.deadline
                    else None
                ),
                "status": a.status.value,
            }
            for a in upcoming
        ],
    }


@router.get("/notifications")
def get_notifications(limit: int = 100):
    limit = max(1, min(limit, 500))

    with db._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                n.event_key,
                n.event_type,
                n.student_id,
                s.name AS student_name,
                n.assignment_id,
                a.title AS assignment_title,
                a.course,
                n.state,
                n.created_at,
                n.attempted_at,
                n.sent_at,
                n.recipient_email,
                n.gmail_message_id,
                n.error_type,
                n.error_message
            FROM notification_events n
            LEFT JOIN students s
                ON s.id = n.student_id
            LEFT JOIN assignments a
                ON a.id = n.assignment_id
            ORDER BY COALESCE(
                n.sent_at,
                n.attempted_at,
                n.created_at
            ) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]
