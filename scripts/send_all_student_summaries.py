from app.config import load_settings
from app.database import Database
from app.students import list_students
from app.email_templates import build_assignment_summary
from app.email_notifications import send_email


db = Database(load_settings().database_path)

students = [
    student
    for student in list_students(db)
    if student.enabled
]

if not students:
    raise SystemExit("No enabled students found.")

print(f"Preparing emails for {len(students)} student(s).")

sent = 0
failed = 0

for student in students:
    print()
    print(f"Student: {student.name}")
    print(f"Email: {student.email}")

    assignments = db.list_assignments(
        student_id=student.id
    )

    try:
        subject, text_body, html_body = (
            build_assignment_summary(
                student,
                assignments,
            )
        )

        send_email(
            subject,
            text_body,
            student.email,
            html_body=html_body,
        )

        print("Status: SENT")
        sent += 1

    except Exception as exc:
        print(
            f"Status: FAILED ({type(exc).__name__})"
        )
        failed += 1


print()
print("=" * 50)
print(f"Students: {len(students)}")
print(f"Sent: {sent}")
print(f"Failed: {failed}")

raise SystemExit(1 if failed else 0)
