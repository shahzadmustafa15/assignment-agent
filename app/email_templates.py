"""Professional HTML email templates for Assignment Agent."""

from datetime import timedelta
from html import escape

from app.models import LOCAL_TIMEZONE, Status, utc_now


def _deadline_text(assignment):
    if assignment.deadline is None:
        return "Deadline unavailable"

    return assignment.deadline.astimezone(
        LOCAL_TIMEZONE
    ).strftime("%d %b %Y, %I:%M %p")


def _card(assignment, label, accent):
    course = escape(assignment.course or "Unknown course")
    title = escape(assignment.title or "Untitled assignment")
    deadline = escape(_deadline_text(assignment))

    status_text = (
        "Due Date Exceeded"
        if label == "Overdue"
        else "Not Submitted"
    )

    return f"""
    <div style="
        border:1px solid #e5e7eb;
        border-left:4px solid {accent};
        border-radius:10px;
        padding:16px;
        margin:12px 0;
        background:#ffffff;
    ">
        <div style="
            font-size:12px;
            font-weight:700;
            color:{accent};
            text-transform:uppercase;
            margin-bottom:6px;
        ">
            {escape(label)}
        </div>

        <div style="
            font-size:17px;
            font-weight:700;
            color:#111827;
            margin-bottom:6px;
        ">
            {title}
        </div>

        <div style="
            font-size:14px;
            color:#4b5563;
            margin-bottom:10px;
        ">
            {course}
        </div>

        <table cellpadding="0" cellspacing="0"
               style="font-size:14px;color:#374151;">
            <tr>
                <td style="padding-right:10px;">
                    <strong>Deadline:</strong>
                </td>
                <td>{deadline}</td>
            </tr>
            <tr>
                <td style="padding-right:10px;padding-top:5px;">
                    <strong>Status:</strong>
                </td>
                <td style="padding-top:5px;">
                    {escape(status_text)}
                </td>
            </tr>
        </table>
    </div>
    """


def build_assignment_summary(student, assignments, now=None):
    now = now or utc_now()
    local_now = now.astimezone(LOCAL_TIMEZONE)

    pending = [
        item
        for item in assignments
        if item.status is not Status.SUBMITTED
    ]

    overdue = []
    due_today = []
    due_tomorrow = []
    due_soon = []
    later = []

    today = local_now.date()
    tomorrow = today + timedelta(days=1)

    for assignment in pending:
        if assignment.deadline is None:
            later.append(assignment)
            continue

        deadline = assignment.deadline.astimezone(
            LOCAL_TIMEZONE
        )

        due_date = deadline.date()

        if deadline < local_now:
            overdue.append(assignment)

        elif due_date == today:
            due_today.append(assignment)

        elif due_date == tomorrow:
            due_tomorrow.append(assignment)

        elif deadline <= local_now + timedelta(days=3):
            due_soon.append(assignment)

        else:
            later.append(assignment)

    urgent_count = len(overdue) + len(due_today)

    due_soon_count = (
        len(due_today)
        + len(due_tomorrow)
        + len(due_soon)
    )

    if urgent_count:
        subject = (
            f"Assignment Alert — "
            f"{urgent_count} Urgent Item"
            f"{'s' if urgent_count != 1 else ''}"
        )
    elif due_soon_count:
        subject = (
            f"Assignment Update — "
            f"{due_soon_count} Deadline"
            f"{'s' if due_soon_count != 1 else ''} Coming Up"
        )
    elif pending:
        subject = (
            f"Assignment Summary — "
            f"{len(pending)} Pending Assignment"
            f"{'s' if len(pending) != 1 else ''}"
        )
    else:
        subject = "Assignment Summary — You're All Caught Up"

    name = escape(student.name)

    sections = []

    if overdue:
        sections.append(
            """
            <h2 style="
                font-size:18px;
                color:#991b1b;
                margin-top:28px;
            ">
                Overdue
            </h2>
            """
            + "".join(
                _card(a, "Overdue", "#dc2626")
                for a in overdue
            )
        )

    if due_today:
        sections.append(
            """
            <h2 style="
                font-size:18px;
                color:#9a3412;
                margin-top:28px;
            ">
                Due Today
            </h2>
            """
            + "".join(
                _card(a, "Due Today", "#ea580c")
                for a in due_today
            )
        )

    if due_tomorrow:
        sections.append(
            """
            <h2 style="
                font-size:18px;
                color:#92400e;
                margin-top:28px;
            ">
                Due Tomorrow
            </h2>
            """
            + "".join(
                _card(a, "Due Tomorrow", "#d97706")
                for a in due_tomorrow
            )
        )

    if due_soon:
        sections.append(
            """
            <h2 style="
                font-size:18px;
                color:#1d4ed8;
                margin-top:28px;
            ">
                Upcoming Deadlines
            </h2>
            """
            + "".join(
                _card(a, "Due Soon", "#2563eb")
                for a in due_soon
            )
        )

    if later:
        sections.append(
            """
            <h2 style="
                font-size:18px;
                color:#374151;
                margin-top:28px;
            ">
                Other Pending Assignments
            </h2>
            """
            + "".join(
                _card(a, "Pending", "#6b7280")
                for a in later
            )
        )

    if not pending:
        sections.append(
            """
            <div style="
                background:#ecfdf5;
                border:1px solid #a7f3d0;
                border-radius:10px;
                padding:20px;
                margin-top:24px;
                color:#065f46;
                text-align:center;
            ">
                <strong>No pending assignments.</strong><br>
                You're currently all caught up.
            </div>
            """
        )

    html_body = f"""
    <!doctype html>
    <html>
    <body style="
        margin:0;
        padding:0;
        background:#f3f4f6;
        font-family:Arial,Helvetica,sans-serif;
    ">

    <div style="
        max-width:680px;
        margin:0 auto;
        padding:24px 12px;
    ">

        <div style="
            background:#111827;
            color:#ffffff;
            padding:28px 24px;
            border-radius:14px 14px 0 0;
        ">
            <div style="
                font-size:13px;
                opacity:.8;
                margin-bottom:6px;
            ">
                ASSIGNMENT AGENT
            </div>

            <div style="
                font-size:27px;
                font-weight:700;
            ">
                Assignment Update
            </div>

            <div style="
                margin-top:8px;
                font-size:15px;
                opacity:.9;
            ">
                {name}
            </div>
        </div>

        <div style="
            background:#ffffff;
            padding:24px;
            border-radius:0 0 14px 14px;
        ">

            <p style="
                font-size:15px;
                line-height:1.6;
                color:#374151;
                margin-top:0;
            ">
                Hi {name},
            </p>

            <p style="
                font-size:15px;
                line-height:1.6;
                color:#374151;
            ">
                Here is your latest assignment update from
                Bahria LMS.
            </p>

            <div style="
                display:block;
                background:#f9fafb;
                border:1px solid #e5e7eb;
                border-radius:10px;
                padding:18px;
                margin:22px 0;
            ">
                <table width="100%" cellpadding="0"
                       cellspacing="0">
                    <tr>
                        <td align="center">
                            <div style="
                                font-size:24px;
                                font-weight:700;
                                color:#111827;
                            ">
                                {len(pending)}
                            </div>
                            <div style="
                                font-size:12px;
                                color:#6b7280;
                            ">
                                Pending
                            </div>
                        </td>

                        <td align="center">
                            <div style="
                                font-size:24px;
                                font-weight:700;
                                color:#d97706;
                            ">
                                {due_soon_count}
                            </div>
                            <div style="
                                font-size:12px;
                                color:#6b7280;
                            ">
                                Due Soon
                            </div>
                        </td>

                        <td align="center">
                            <div style="
                                font-size:24px;
                                font-weight:700;
                                color:#dc2626;
                            ">
                                {len(overdue)}
                            </div>
                            <div style="
                                font-size:12px;
                                color:#6b7280;
                            ">
                                Overdue
                            </div>
                        </td>
                    </tr>
                </table>
            </div>

            {''.join(sections)}

            <div style="
                border-top:1px solid #e5e7eb;
                margin-top:30px;
                padding-top:20px;
                font-size:14px;
                line-height:1.6;
                color:#6b7280;
            ">
                Please review Bahria LMS and make sure that
                completed assignments have been submitted
                successfully.
            </div>

            <div style="
                margin-top:22px;
                font-size:14px;
                color:#374151;
            ">
                Regards,<br>
                <strong>Assignment Agent</strong><br>
                Academic Assignment Monitoring System
            </div>

        </div>

        <div style="
            text-align:center;
            padding:18px;
            font-size:11px;
            color:#9ca3af;
        ">
            Automated assignment monitoring notification
        </div>

    </div>

    </body>
    </html>
    """

    text_lines = [
        f"Hi {student.name},",
        "",
        "Here is your latest Bahria LMS assignment update.",
        "",
        f"Pending assignments: {len(pending)}",
        f"Due soon: {due_soon_count}",
        f"Overdue: {len(overdue)}",
        "",
    ]

    groups = [
        ("OVERDUE", overdue),
        ("DUE TODAY", due_today),
        ("DUE TOMORROW", due_tomorrow),
        ("UPCOMING", due_soon),
        ("OTHER PENDING", later),
    ]

    for heading, items in groups:
        if not items:
            continue

        text_lines.extend(
            [
                heading,
                "-" * len(heading),
            ]
        )

        for assignment in items:
            text_lines.extend(
                [
                    assignment.title,
                    f"Course: {assignment.course}",
                    f"Deadline: {_deadline_text(assignment)}",
                    (
                        "Status: Due Date Exceeded"
                        if heading == "OVERDUE"
                        else "Status: Not Submitted"
                    ),
                    "",
                ]
            )

    if not pending:
        text_lines.append("No pending assignments.")

    text_lines.extend(
        [
            "",
            "Please verify completed submissions through Bahria LMS.",
            "",
            "Assignment Agent",
            "Academic Assignment Monitoring System",
        ]
    )

    return subject, "\n".join(text_lines), html_body


def build_assignment_event(student_name, assignment, kind):
    """Build one professional assignment notification email."""

    labels = {
        "NEW_ASSIGNMENT": {
            "subject": "New Assignment",
            "heading": "New Assignment Detected",
            "label": "New Assignment",
            "accent": "#2563eb",
            "intro": (
                "A new assignment has been detected on your "
                "Bahria LMS."
            ),
        },
        "DUE_TOMORROW": {
            "subject": "Assignment Due Tomorrow",
            "heading": "Deadline Reminder",
            "label": "Due Tomorrow",
            "accent": "#d97706",
            "intro": (
                "You have an assignment due tomorrow. "
                "Please make sure it is completed and "
                "submitted on Bahria LMS."
            ),
        },
        "DUE_TODAY": {
            "subject": "Assignment Due Today",
            "heading": "Urgent Deadline Reminder",
            "label": "Due Today",
            "accent": "#dc2626",
            "intro": (
                "This assignment is due today. "
                "Please review its submission status as soon "
                "as possible."
            ),
        },
        "OVERDUE": {
            "subject": "Overdue Assignment",
            "heading": "Action Required",
            "label": "Overdue",
            "accent": "#b91c1c",
            "intro": (
                "The deadline for this assignment has passed "
                "and Bahria LMS still shows it as not submitted. "
                "Please review the assignment and confirm your "
                "submission status."
            ),
        },
    }

    if kind not in labels:
        raise ValueError(
            f"Unsupported assignment email event: {kind}"
        )

    config = labels[kind]

    name = escape(student_name or "Student")
    course = escape(
        assignment.course or "Unknown course"
    )
    title = escape(
        assignment.title or "Untitled assignment"
    )
    deadline = escape(
        _deadline_text(assignment)
    )

    subject = (
        f"{config['subject']} — "
        f"{' '.join((assignment.title or assignment.course).splitlines())}"
    )

    text_body = "\n".join(
        [
            f"Hi {student_name},",
            "",
            config["intro"],
            "",
            f"Assignment: {assignment.title}",
            f"Course: {assignment.course}",
            f"Deadline: {_deadline_text(assignment)}",
            (
                "Status: Due Date Exceeded"
                if kind == "OVERDUE"
                else "Status: Not Submitted"
            ),
            "",
            "Please verify your submission through Bahria LMS.",
            "",
            "Assignment Agent",
            "Academic Assignment Monitoring System",
        ]
    )

    html_body = f"""
    <!doctype html>
    <html>
    <body style="
        margin:0;
        padding:0;
        background:#f3f4f6;
        font-family:Arial,Helvetica,sans-serif;
    ">

    <div style="
        max-width:680px;
        margin:0 auto;
        padding:24px 12px;
    ">

        <div style="
            background:#111827;
            color:#ffffff;
            padding:28px 24px;
            border-radius:14px 14px 0 0;
        ">
            <div style="
                font-size:13px;
                opacity:.8;
                margin-bottom:6px;
            ">
                ASSIGNMENT AGENT
            </div>

            <div style="
                font-size:27px;
                font-weight:700;
            ">
                {escape(config["heading"])}
            </div>

            <div style="
                margin-top:8px;
                font-size:15px;
                opacity:.9;
            ">
                {name}
            </div>
        </div>

        <div style="
            background:#ffffff;
            padding:24px;
            border-radius:0 0 14px 14px;
        ">

            <p style="
                font-size:15px;
                line-height:1.6;
                color:#374151;
                margin-top:0;
            ">
                Hi {name},
            </p>

            <p style="
                font-size:15px;
                line-height:1.6;
                color:#374151;
            ">
                {escape(config["intro"])}
            </p>

            <div style="
                border:1px solid #e5e7eb;
                border-left:4px solid {config["accent"]};
                border-radius:10px;
                padding:18px;
                margin:24px 0;
                background:#ffffff;
            ">

                <div style="
                    font-size:12px;
                    font-weight:700;
                    color:{config["accent"]};
                    text-transform:uppercase;
                    margin-bottom:7px;
                ">
                    {escape(config["label"])}
                </div>

                <div style="
                    font-size:19px;
                    font-weight:700;
                    color:#111827;
                    margin-bottom:7px;
                ">
                    {title}
                </div>

                <div style="
                    font-size:14px;
                    color:#4b5563;
                    margin-bottom:16px;
                ">
                    {course}
                </div>

                <table
                    cellpadding="0"
                    cellspacing="0"
                    style="
                        font-size:14px;
                        color:#374151;
                    "
                >
                    <tr>
                        <td style="
                            padding-right:14px;
                            padding-bottom:7px;
                        ">
                            <strong>Deadline:</strong>
                        </td>

                        <td style="padding-bottom:7px;">
                            {deadline}
                        </td>
                    </tr>

                    <tr>
                        <td style="
                            padding-right:14px;
                        ">
                            <strong>Status:</strong>
                        </td>

                        <td>
                            Not Submitted
                        </td>
                    </tr>
                </table>

            </div>

            <div style="
                background:#f9fafb;
                border:1px solid #e5e7eb;
                border-radius:10px;
                padding:16px;
                font-size:14px;
                line-height:1.6;
                color:#4b5563;
            ">
                Please verify completed work directly in
                Bahria LMS to make sure the submission was
                successful.
            </div>

            <div style="
                margin-top:24px;
                font-size:14px;
                line-height:1.6;
                color:#374151;
            ">
                Regards,<br>
                <strong>Assignment Agent</strong><br>
                Academic Assignment Monitoring System
            </div>

        </div>

        <div style="
            text-align:center;
            padding:18px;
            font-size:11px;
            color:#9ca3af;
        ">
            Automated assignment monitoring notification
        </div>

    </div>

    </body>
    </html>
    """

    return subject, text_body, html_body

def build_deadline_change_event(
    student_name,
    assignment,
    kind,
    previous_deadline,
):
    """Build a professional deadline-change notification email."""
    from datetime import datetime
    from html import escape
    from zoneinfo import ZoneInfo

    timezone = ZoneInfo("Asia/Karachi")

    configs = {
        "DEADLINE_EXTENDED": {
            "subject": "Assignment Deadline Extended",
            "heading": "Deadline Extended",
            "label": "Extended",
            "accent": "#2563eb",
            "intro": (
                "The deadline for this assignment has been "
                "extended on Bahria LMS."
            ),
        },
        "DEADLINE_SHORTENED": {
            "subject": "Assignment Deadline Shortened",
            "heading": "Deadline Shortened",
            "label": "Deadline Shortened",
            "accent": "#dc2626",
            "intro": (
                "The deadline for this assignment has been "
                "moved to an earlier date. Please review the "
                "new deadline carefully."
            ),
        },
    }

    if kind not in configs:
        raise ValueError(
            f"Unsupported deadline-change event: {kind}"
        )

    config = configs[kind]

    def format_deadline(value):
        if not value:
            return "Unknown"

        if isinstance(value, str):
            value = datetime.fromisoformat(value)

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone)

        value = value.astimezone(timezone)

        return value.strftime(
            "%B %d, %Y, %I:%M %p"
        )

    old_deadline = format_deadline(previous_deadline)
    new_deadline = format_deadline(
        assignment.deadline
    )

    name = escape(
        student_name or "Student"
    )

    course = escape(
        assignment.course or "Unknown course"
    )

    title = escape(
        assignment.title or "Untitled assignment"
    )

    subject_title = " ".join(
        (
            assignment.title
            or assignment.course
            or "Assignment"
        ).splitlines()
    )

    subject = (
        f"{config['subject']} — {subject_title}"
    )

    text_body = "\n".join(
        [
            f"Hi {student_name},",
            "",
            config["intro"],
            "",
            f"Course: {assignment.course}",
            f"Assignment: {assignment.title}",
            f"Previous Deadline: {old_deadline}",
            f"New Deadline: {new_deadline}",
            "Status: Not Submitted",
            "",
            "Please review the updated deadline on Bahria LMS.",
            "",
            "Assignment Agent",
            "Academic Assignment Monitoring System",
        ]
    )

    html_body = f"""
<!doctype html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#f3f4f6;
    font-family:Arial,Helvetica,sans-serif;
">

<div style="
    max-width:680px;
    margin:0 auto;
    padding:24px 12px;
">

    <div style="
        background:#111827;
        color:#ffffff;
        padding:28px 24px;
        border-radius:14px 14px 0 0;
    ">

        <div style="
            font-size:13px;
            opacity:.8;
            margin-bottom:6px;
        ">
            ASSIGNMENT AGENT
        </div>

        <div style="
            font-size:27px;
            font-weight:700;
        ">
            {escape(config["heading"])}
        </div>

        <div style="
            margin-top:8px;
            font-size:15px;
            opacity:.9;
        ">
            {name}
        </div>
    </div>

    <div style="
        background:#ffffff;
        padding:24px;
        border-radius:0 0 14px 14px;
    ">

        <p style="
            font-size:15px;
            line-height:1.6;
            color:#374151;
            margin-top:0;
        ">
            Hi {name},
        </p>

        <p style="
            font-size:15px;
            line-height:1.6;
            color:#374151;
        ">
            {escape(config["intro"])}
        </p>

        <div style="
            border-left:5px solid {config['accent']};
            background:#f9fafb;
            padding:20px;
            border-radius:10px;
            margin:22px 0;
        ">

            <div style="
                font-size:12px;
                font-weight:700;
                text-transform:uppercase;
                color:{config['accent']};
                margin-bottom:12px;
            ">
                {escape(config["label"])}
            </div>

            <div style="
                font-size:20px;
                font-weight:700;
                color:#111827;
                margin-bottom:5px;
            ">
                {title}
            </div>

            <div style="
                font-size:14px;
                color:#6b7280;
                margin-bottom:18px;
            ">
                {course}
            </div>

            <table style="
                width:100%;
                border-collapse:collapse;
                font-size:14px;
                color:#374151;
            ">

                <tr>
                    <td style="
                        padding:8px 0;
                        font-weight:600;
                        width:160px;
                    ">
                        Previous Deadline
                    </td>

                    <td style="padding:8px 0;">
                        {escape(old_deadline)}
                    </td>
                </tr>

                <tr>
                    <td style="
                        padding:8px 0;
                        font-weight:600;
                    ">
                        New Deadline
                    </td>

                    <td style="
                        padding:8px 0;
                        font-weight:700;
                        color:{config['accent']};
                    ">
                        {escape(new_deadline)}
                    </td>
                </tr>

                <tr>
                    <td style="
                        padding:8px 0;
                        font-weight:600;
                    ">
                        Status
                    </td>

                    <td style="padding:8px 0;">
                        Not Submitted
                    </td>
                </tr>

            </table>

        </div>

        <p style="
            font-size:14px;
            line-height:1.6;
            color:#6b7280;
        ">
            Please verify the updated deadline through Bahria LMS.
        </p>

        <div style="
            margin-top:28px;
            padding-top:18px;
            border-top:1px solid #e5e7eb;
            font-size:12px;
            color:#9ca3af;
        ">
            Assignment Agent<br>
            Academic Assignment Monitoring System
        </div>

    </div>
</div>

</body>
</html>
"""

    return subject, text_body, html_body
