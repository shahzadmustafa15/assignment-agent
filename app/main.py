"""Assignment CLI with manual read-only Gmail access and local reminders."""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import fields
from datetime import datetime
from enum import Enum

from app.config import load_settings
from app.database import AssignmentNotFoundError, Database
from app.models import LOCAL_TIMEZONE, Status
from app.notifier import DesktopNotifier, NotificationError
from app.scheduler import check_reminders, daily_summary
from app.lms.base import LMSError


def display(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(LOCAL_TIMEZONE).isoformat(timespec="seconds")
    if isinstance(value, Enum):
        return value.value
    return "—" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local assignment records (Asia/Karachi)")
    commands = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("assignments", "List all assignments"),
        ("new", "List assignments with status NEW"),
        ("upcoming", "List unsubmitted assignments with future deadlines"),
        ("overdue", "Mark past deadlines OVERDUE and list overdue assignments"),
    ):
        commands.add_parser(name, help=help_text)
    for name, help_text in (
        ("details", "Show one assignment"),
        ("mark-submitted", "Record an already completed submission locally"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("assignment_id", type=int)
    delete = commands.add_parser("delete", help="Delete a local assignment only")
    delete.add_argument("assignment_id", type=int)
    delete.add_argument("--yes", action="store_true", help="Skip local deletion confirmation")
    commands.add_parser("check-reminders", help="Send unsent deadline reminders once")
    commands.add_parser("email-assignment-report", help="Scan all current LMS courses and email one report")
    commands.add_parser("test-email", help="Send one test email to ALERT_EMAIL")
    commands.add_parser("test-notification", help="Send a harmless desktop notification")
    summary = commands.add_parser("daily-summary", help="Print the assignment summary")
    summary.add_argument("--notify", action="store_true", help="Also send the summary to the desktop")

    summary_all = commands.add_parser(
        "daily-summary-all",
        help="Show separate daily summaries for all enabled students",
    )
    summary_all.add_argument(
        "--notify",
        action="store_true",
        help="Send a separate desktop summary for each student",
    )
    auth = commands.add_parser("gmail-auth", help="Authorize Gmail read and send permissions in your browser")
    auth.add_argument("--force", action="store_true", help="Replace an invalid/revoked token through new consent")
    scan = commands.add_parser("gmail-scan", help="Scan recent Gmail messages manually")
    scan.add_argument("--dry-run", action="store_true", help="Classify only; no SQLite access or notifications")
    scan.add_argument("--discovery-debug", action="store_true", help="Show queries, ID counts, deduplication, fallback and metadata counts")
    scan.add_argument("--verbose", action="store_true", help="Show candidate subject, sender, score, and reasons")
    commands.add_parser("gmail-status", help="Check authorization usability without exposing tokens")

    student_add = commands.add_parser(
        "student-add",
        help="Add a student to the multi-user assignment system",
    )
    student_add.add_argument(
        "--name",
        help="Student name; prompted if omitted",
    )
    student_add.add_argument(
        "--email",
        help="Notification email; prompted if omitted",
    )
    student_add.add_argument(
        "--id",
        dest="student_id",
        help="Optional custom student ID",
    )

    commands.add_parser(
        "student-list",
        help="List students registered in the system",
    )

    student_auth = commands.add_parser(
        "student-auth",
        help="Authenticate one student's Bahria CMS/LMS session",
    )
    student_auth.add_argument(
        "student_id",
        help="Student ID from student-list",
    )

    student_scan = commands.add_parser(
        "student-scan",
        help="Scan one student's Bahria LMS courses",
    )
    student_scan.add_argument(
        "student_id",
        help="Student ID from student-list",
    )
    student_scan.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only assignment comparison; no notifications (browser session may refresh)",
    )
    student_scan.add_argument(
        "--verbose",
        action="store_true",
        help="Show safe per-assignment results",
    )

    scan_all = commands.add_parser(
        "scan-all-students",
        help="Scan all enabled students independently",
    )
    scan_all.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only assignment comparison; no notifications (browser sessions may refresh)",
    )
    scan_all.add_argument(
        "--verbose",
        action="store_true",
        help="Show safe per-assignment results",
    )

    for name in ("lms-auth", "lms-status", "lms-inspect"):
        commands.add_parser(name, help="Manual LMS session: " + name)
    lms_scan = commands.add_parser("lms-scan", help="Discover and scan current registered LMS courses")
    lms_scan.add_argument("--dry-run", action="store_true", help="Read-only database comparison; no notifications")
    lms_scan.add_argument("--verbose", action="store_true", help="Show safe per-assignment results")
    lms_scan.add_argument("--configure-background", action="store_true",
                          help="Save and verify this course page for unattended scans; does not install a timer")
    lms_scan.add_argument("--single-course", action="store_true", help="Use the original interactive single-course scan")
    courses = commands.add_parser("lms-courses", help="Inspect registered current LMS courses without writes")
    courses.add_argument("--dry-run", action="store_true", help="Course discovery is always read-only")
    courses.add_argument("--verbose", action="store_true", help="Show safe course navigation evidence")
    args = parser.parse_args(argv)
    if args.command == "lms-scan" and args.configure_background and args.dry_run:
        parser.error("--configure-background cannot be combined with --dry-run")
    try:
        if args.command == "email-assignment-report":
            from app.assignment_report import email_assignment_report
            return email_assignment_report()
        if args.command == "test-email":
            from app.email_notifications import send_test_email
            from app.gmail_auth import GmailError
            try:
                send_test_email()
            except GmailError as exc:
                print(f"Email: {exc}", file=sys.stderr)
                return 1
            print("Test email sent.")
            return 0
        if args.command == "test-notification":
            DesktopNotifier().send("Assignment Agent test", "Desktop notifications are working. This is only a test.")
            print("Test notification handed to notify-send.")
            return 0
        if args.command in ("gmail-auth", "gmail-scan", "gmail-status"):
            return gmail_command(args)
        if args.command in ("lms-auth", "lms-status", "lms-inspect", "lms-scan", "lms-courses"):
            from app.lms.base import load_settings as load_lms_settings
            from app.lms.bahria import BahriaLMSAdapter
            try:
                lms_settings = load_lms_settings()
                if args.command == "lms-courses":
                    from app.lms.background import run_background
                    return run_background(lms_settings, load_settings(), dry_run=True,
                                          multi_course=True, discover_only=True, verbose=args.verbose)
                if args.command == "lms-scan" and os.environ.get("ASSIGNMENT_LMS_BACKGROUND") == "1":
                    if args.configure_background:
                        raise LMSError("Background mode cannot configure an interactive target.")
                    from app.lms.background import run_background
                    return run_background(lms_settings, load_settings(), dry_run=args.dry_run,
                                          multi_course=not args.single_course, verbose=args.verbose)
                adapter = BahriaLMSAdapter(lms_settings)
                if args.command == "lms-scan":
                    if args.configure_background:
                        return adapter.run(args.command, dry_run=False, verbose=args.verbose, configure_background=True)
                    if args.single_course:
                        return adapter.run(args.command, dry_run=args.dry_run, verbose=args.verbose)
                    from app.lms.background import run_background
                    return run_background(lms_settings, load_settings(), dry_run=args.dry_run,
                                          multi_course=True, verbose=args.verbose)
                return adapter.run(args.command)
            except LMSError as exc:
                print(f"LMS: {exc}", file=sys.stderr)
                return 1
        settings = load_settings()
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_path)

        if args.command == "scan-all-students":
            from app.students import list_students

            students = [
                student
                for student in list_students(database)
                if student.enabled
            ]

            if not students:
                print("No enabled students registered.")
                return 0

            print(
                f"Scanning {len(students)} enabled student(s)."
            )

            failures = []

            for student in students:
                print()
                print(
                    "=" * 60
                )
                print(
                    f"Student: {student.name} ({student.id})"
                )
                print(
                    "=" * 60
                )

                child_args = [
                    "student-scan",
                    student.id,
                ]

                if args.dry_run:
                    child_args.append("--dry-run")

                if args.verbose:
                    child_args.append("--verbose")

                try:
                    code = main(child_args)
                except Exception:
                    code = 1

                if code != 0:
                    failures.append(student.id)

            print()
            print(
                f"Students attempted: {len(students)}"
            )
            print(
                f"Successful: {len(students) - len(failures)}"
            )
            print(
                f"Failed: {len(failures)}"
            )

            if failures:
                print(
                    "Failed students: "
                    + ", ".join(failures)
                )

            return 1 if failures else 0

        elif args.command == "student-scan":
            from pathlib import Path
            import json

            from app.students import get_student, student_session_path
            from app.lms.base import LMSSettings
            from app.lms.background import run_background

            student = get_student(
                database,
                args.student_id,
            )

            if student is None:
                raise ValueError(
                    f"Unknown student: {args.student_id}"
                )

            if not student.enabled:
                raise ValueError(
                    f"Student is disabled: {student.id}"
                )

            project_root = (
                Path(__file__).resolve().parent.parent
            )

            state_path = student_session_path(student, project_root)

            portal_path = state_path.with_name(
                "bahria_portal.json"
            )

            try:
                portal_data = json.loads(
                    portal_path.read_text()
                ) if portal_path.exists() else {}
                portal_url = portal_data.get(
                    "portal_url",
                    "",
                )
            except (
                OSError,
                ValueError,
                TypeError,
                AttributeError,
            ):
                raise LMSError(
                    "Student portal configuration is invalid."
                ) from None

            if not isinstance(portal_url, str):
                raise LMSError(
                    "Student portal URL is invalid."
                )

            settings_for_student = LMSSettings(
                portal_url=portal_url.strip(),
                state_path=state_path,
                profile_path=state_path.with_name('browser-profile'),
            )

            print(
                f"Scanning student: "
                f"{student.name} ({student.id})"
            )

            return run_background(
                settings_for_student,
                settings,
                dry_run=args.dry_run,
                verbose=args.verbose,
                multi_course=True,
                student_id=student.id,
            )

        elif args.command == "student-auth":
            from pathlib import Path
            import json

            from app.students import get_student, set_auth_status, student_session_path
            from app.lms.base import LMSSettings
            from app.lms.bahria import BahriaLMSAdapter

            student = get_student(database, args.student_id)

            if student is None:
                raise ValueError(
                    f"Unknown student: {args.student_id}"
                )

            project_root = Path(__file__).resolve().parent.parent

            state_path = student_session_path(student, project_root)

            # Keep each student's portal configuration next to
            # that student's private browser state.
            portal_path = state_path.with_name(
                "bahria_portal.json"
            )

            portal_url = ""

            if portal_path.exists():
                try:
                    data = json.loads(
                        portal_path.read_text()
                    )
                    value = data.get("portal_url", "")

                    if isinstance(value, str):
                        portal_url = value.strip()
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    AttributeError,
                ):
                    raise LMSError(
                        "Student portal configuration is invalid."
                    ) from None

            settings_for_student = LMSSettings(
                portal_url=portal_url,
                state_path=state_path,
                profile_path=state_path.with_name('browser-profile'),
            )

            print(
                f"Authenticating student: "
                f"{student.name} ({student.id})"
            )

            print(
                "The student must enter their own Bahria "
                "credentials in Chromium."
            )

            print(
                "The Assignment Agent will not ask for or "
                "store their password."
            )

            adapter = BahriaLMSAdapter(
                settings_for_student
            )

            from app.lms.background import scan_lock, clear_auth_notice
            with scan_lock(state_path.parent) as acquired:
                if not acquired:
                    raise LMSError('This student has an active scan or authentication browser; retry after it finishes.')
                result = adapter.run("lms-auth", configure_background=True)
                if result == 0:
                    clear_auth_notice(settings, student.id)
                    set_auth_status(database, student.id, "ACTIVE")

            if result == 0:
                print(
                    f"Student authentication ready: "
                    f"{student.id}"
                )

                print(
                    f"Session: "
                    f"{student.session_path}"
                )

                return 0

            return result

        elif args.command == "student-list":
            from app.students import list_students

            students = list_students(database)

            if not students:
                print("No students registered.")
                return 0

            for student in students:
                state = "enabled" if student.enabled else "disabled"

                print(
                    f"{student.id} | "
                    f"{student.name} | "
                    f"{student.email} | "
                    f"{state} | "
                    f"auth={student.auth_status}"
                )

            return 0

        elif args.command == "student-add":
            from app.students import add_student

            name = args.name

            if not name:
                try:
                    name = input("Student name: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nStudent creation cancelled.")
                    return 1

            email = args.email

            if not email:
                try:
                    email = input(
                        "Notification email: "
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nStudent creation cancelled.")
                    return 1

            student = add_student(
                database,
                name=name,
                email=email,
                student_id=args.student_id,
            )

            print("Student added successfully.")
            print(f"ID: {student.id}")
            print(f"Name: {student.name}")
            print(f"Email: {student.email}")
            print(f"Auth status: {student.auth_status}")
            print(f"Session: {student.session_path}")

            return 0

        elif args.command == "delete":
            assignment = database.get_assignment(args.assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(f"Assignment {args.assignment_id} not found; nothing deleted")
            if not args.yes:
                # Escape control characters from imported email titles in the prompt.
                title = json.dumps(assignment.title, ensure_ascii=False)
                try:
                    answer = input(f"Delete assignment {assignment.id}: {title}? [y/N] ")
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer.strip().lower() not in ("y", "yes"):
                    print("Deletion cancelled; nothing deleted.")
                    return 0
            if not database.delete_assignment(assignment.id):
                raise AssignmentNotFoundError(f"Assignment {assignment.id} not found; nothing deleted")
            print(f"Deleted local assignment {assignment.id}. No external services were modified.")
        elif args.command == "check-reminders":
            result = check_reminders(database, DesktopNotifier())
            print(f"Reminders sent: {result.sent}; already sent or changed: {result.skipped}; "
                  f"failed: {len(result.errors)}")
            for error in result.errors:
                print(error, file=sys.stderr)
            return 1 if result.errors else 0
        elif args.command == "daily-summary-all":
            from app.students import list_students

            students = [
                student
                for student in list_students(database)
                if student.enabled
            ]

            if not students:
                print("No enabled students registered.")
                return 0

            for student in students:
                summary_text = daily_summary(
                    database,
                    student_id=student.id,
                )

                print()
                print("=" * 60)
                print(
                    f"{student.name} ({student.id})"
                )
                print("=" * 60)
                print(summary_text)

                if args.notify:
                    DesktopNotifier().send(
                        f"Assignment summary - {student.name}",
                        summary_text,
                    )

            return 0

        elif args.command == "daily-summary":
            summary_text = daily_summary(database)
            print(summary_text)
            if args.notify:
                DesktopNotifier().send("Assignment summary", summary_text)
        elif args.command is None:
            print("Assignment Monitoring System: Phase 3 local reminders ready.")
            print(f"Database: {settings.database_path}")
            print("Gmail scanning is manual; external submission is not implemented.")
        elif args.command in ("details", "mark-submitted"):
            if args.command == "mark-submitted":
                assignment = database.mark_submitted(args.assignment_id)
                print("Local status updated only; no files were submitted.")
            else:
                assignment = database.get_assignment(args.assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(f"Assignment {args.assignment_id} not found")
            for field in fields(assignment):
                print(f"{field.name}: {display(getattr(assignment, field.name))}")
        else:
            if args.command == "overdue":
                database.mark_overdue()
                assignments = database.list_by_status(Status.OVERDUE)
            elif args.command == "upcoming":
                assignments = database.upcoming()
            elif args.command == "new":
                assignments = database.list_by_status(Status.NEW)
            else:
                assignments = database.list_assignments()
            if not assignments:
                print("No assignments found.")
            for assignment in assignments:
                print(f"{assignment.id} | {assignment.status.value} | {assignment.course} | "
                      f"{assignment.title} | {display(assignment.deadline)}")
        return 0
    except LMSError as exc:
        print(f"LMS: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.Error, AssignmentNotFoundError, NotificationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def gmail_command(args) -> int:
    # Keep Gmail dependencies and configuration out of the existing local timers.
    try:
        from app.config import load_gmail_settings
        from app.gmail_auth import GmailError, authenticate, authorization_status, gmail_service
        from app.gmail_monitor import scan_gmail
    except ImportError:
        print("Gmail dependencies missing. Run python -m pip install -r requirements.txt.", file=sys.stderr)
        return 1
    try:
        settings = load_gmail_settings()
        if args.command == "gmail-auth":
            authenticate(settings, interactive=True, force=args.force)
            print("Gmail read and send authorization saved securely.")
        elif args.command == "gmail-status":
            print(authorization_status(settings))
        else:
            service = gmail_service(settings)
            try:
                database = None if args.dry_run else Database(load_settings().database_path)
                result = scan_gmail(service, database, settings, DesktopNotifier(),
                                    dry_run=args.dry_run, verbose=args.verbose)
            finally:
                service.close()
            if args.discovery_debug:
                for query_report in result.discovery_reports:
                    print(f"Gmail query: {json.dumps(query_report.query)}")
                    print(f"  IDs returned: {query_report.ids_returned}; unique added: {query_report.unique_added}; "
                          f"pages: {query_report.pages}; fallback: {query_report.fallback}; failed: {query_report.failed}")
                print(f"Unique IDs after deduplication: {result.candidate_ids}; duplicate IDs merged: {result.discovery_duplicates}")
                print(f"Fallback scan used: {'yes' if result.fallback_used else 'no'}")
                print(f"Metadata candidate count: {result.metadata_candidates} (of {result.metadata_fetched} metadata fetched)")
            for report in result.classifications:
                # JSON quoting escapes terminal controls and prevents multiline header spoofing.
                subject = json.dumps(report.subject[:300], ensure_ascii=True)
                sender = json.dumps(report.sender[:200], ensure_ascii=True)
                if args.verbose:
                    print(f"Subject: {subject}\nSender: {sender}\n"
                          f"Classification: {report.result.classification}\nScore: {report.result.score}\nReasons:")
                    for reason in report.result.reasons:
                        print(f"* {reason}")
                    print()
                else:
                    print(f"{report.result.classification} | Score: {report.result.score} | Subject: {subject}")
            if args.dry_run:
                print(f"Dry-run: {result.examined} examined; {result.accepted} classified ASSIGNMENT; "
                      f"{result.ignored} rejected; {len(result.errors)} errors. No SQLite writes or notifications.")
                print("Includes previously processed candidates; classification is not a count of new imports.")
            else:
                print(f"Gmail scan: {result.examined} examined; {result.created} new; {result.skipped} skipped; "
                      f"{result.ignored} rejected; {result.notified} notified; {len(result.errors)} errors.")
            print(f"Candidate IDs found: {result.candidate_ids}; metadata fetched: {result.metadata_fetched}; "
                  f"full messages fetched: {result.full_fetched}; assignments classified: {result.accepted}; "
                  f"rejected: {result.ignored}; skipped: {result.skipped}; retries performed: {result.retries}; "
                  f"rate-limit failures: {result.rate_limit_failures}; other errors: {result.other_errors}.")
            if result.truncated:
                print("Scan limit reached; increase GMAIL_MAX_MESSAGES to cover the full window.")
            for error in result.errors:
                print(error, file=sys.stderr)
            return 1 if result.errors else 0
        return 0
    except GmailError as exc:
        print(f"Gmail: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
