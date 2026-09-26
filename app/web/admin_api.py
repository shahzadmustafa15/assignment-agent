import subprocess
from datetime import datetime, timedelta

from fastapi import APIRouter

from app.config import load_settings
from app.database import Database
from app.models import LOCAL_TIMEZONE, Status


router = APIRouter(prefix="/api/admin")

db = Database(load_settings().database_path)


def systemd_status(unit):
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "is-active",
                unit,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        value = result.stdout.strip()

        return value or "unknown"

    except Exception:
        return "unknown"


@router.get("/health")
def admin_health():
    now = datetime.now(LOCAL_TIMEZONE)

    with db._connect() as connection:
        students = connection.execute(
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

        notification_stats = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN state='sent'
                        THEN 1 ELSE 0
                    END
                ) AS sent,
                SUM(
                    CASE
                        WHEN state='failed'
                        THEN 1 ELSE 0
                    END
                ) AS failed,
                SUM(
                    CASE
                        WHEN state='pending'
                        THEN 1 ELSE 0
                    END
                ) AS pending
            FROM notification_events
            """
        ).fetchone()

        latest_notification = connection.execute(
            """
            SELECT
                sent_at,
                attempted_at,
                created_at
            FROM notification_events
            ORDER BY COALESCE(
                sent_at,
                attempted_at,
                created_at
            ) DESC
            LIMIT 1
            """
        ).fetchone()

        student_health = []

        for student in students:
            assignments = db.list_assignments(
                student_id=student["id"]
            )

            submitted = 0
            overdue = 0
            pending = 0

            for assignment in assignments:
                if assignment.status is Status.SUBMITTED:
                    submitted += 1
                    continue

                pending += 1

                if (
                    assignment.deadline is not None
                    and assignment.deadline.astimezone(
                        LOCAL_TIMEZONE
                    ) < now
                ):
                    overdue += 1

            last_scan = connection.execute(
                """
                SELECT MAX(observed_at) AS last_scan
                FROM lms_records
                WHERE student_id=?
                """,
                (student["id"],),
            ).fetchone()

            last_email = connection.execute(
                """
                SELECT MAX(sent_at) AS last_email
                FROM notification_events
                WHERE student_id=?
                  AND state='sent'
                """,
                (student["id"],),
            ).fetchone()

            failed_notifications = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM notification_events
                WHERE student_id=?
                  AND state='failed'
                """,
                (student["id"],),
            ).fetchone()

            last_scan_value = (
                last_scan["last_scan"]
                if last_scan
                else None
            )

            scan_status = "unknown"

            if last_scan_value:
                try:
                    parsed = datetime.fromisoformat(
                        last_scan_value
                    )

                    if parsed.tzinfo is None:
                        parsed = parsed.replace(
                            tzinfo=LOCAL_TIMEZONE
                        )

                    age = now - parsed.astimezone(
                        LOCAL_TIMEZONE
                    )

                    scan_status = (
                        "healthy"
                        if age <= timedelta(hours=2)
                        else "stale"
                    )

                except Exception:
                    scan_status = "unknown"

            student_health.append(
                {
                    "id": student["id"],
                    "name": student["name"],
                    "email": student["email"],
                    "enabled": bool(
                        student["enabled"]
                    ),
                    "assignments": {
                        "total": len(assignments),
                        "submitted": submitted,
                        "pending": pending,
                        "overdue": overdue,
                    },
                    "last_scan": last_scan_value,
                    "scan_status": scan_status,
                    "last_email": (
                        last_email["last_email"]
                        if last_email
                        else None
                    ),
                    "failed_notifications": (
                        failed_notifications["count"]
                        if failed_notifications
                        else 0
                    ),
                }
            )

    timers = {
        "lms": systemd_status(
            "assignment-agent-multiuser-lms.timer"
        ),
        "reminders": systemd_status(
            "assignment-agent-multiuser-reminders.timer"
        ),
        "summary": systemd_status(
            "assignment-agent-multiuser-summary.timer"
        ),
    }

    warnings = []

    for student in student_health:
        if not student["enabled"]:
            continue

        if student["scan_status"] == "stale":
            warnings.append(
                f"{student['name']} has not been scanned recently."
            )

        if student["failed_notifications"] > 0:
            warnings.append(
                f"{student['name']} has failed notifications."
            )

    for name, state in timers.items():
        if state != "active":
            warnings.append(
                f"{name} timer is {state}."
            )

    return {
        "generated_at": now.isoformat(),
        "students": student_health,
        "notifications": {
            "total": notification_stats["total"] or 0,
            "sent": notification_stats["sent"] or 0,
            "failed": notification_stats["failed"] or 0,
            "pending": notification_stats["pending"] or 0,
            "last_activity": (
                dict(latest_notification)
                if latest_notification
                else None
            ),
        },
        "timers": timers,
        "warnings": warnings,
    }
