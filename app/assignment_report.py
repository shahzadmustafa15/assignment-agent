"""One explicitly requested report from a fresh, complete current-course scan."""
from datetime import datetime
import sys

from app.models import LOCAL_TIMEZONE, utc_now
from app.lms.base import LMSError

SUBJECT = 'Bahria Assignment Report — Current Semester'


def clean(value):
    # Prevent imported text from impersonating report headings/fields.
    return ' '.join(str(value or '').split())


def build_report(result, now=None):
    if (result.get('auth_expired') or result['errors'] or result['courses_skipped']
            or result['courses_scanned'] != result['courses_discovered']
            or len(result['courses']) != result['courses_scanned']):
        raise LMSError('Report not sent: current-course scan was incomplete; review scan results.')
    now = now or utc_now()
    rows = result['reports']
    # Distinct external IDs are already deduplicated by the shared scanner.
    submitted = sum(r['Local status'] == 'SUBMITTED' for r in rows)
    overdue = sum(r['Local status'] != 'SUBMITTED' and r['Parsed deadline'] != 'Unknown'
                  and datetime.fromisoformat(r['Parsed deadline']) < now for r in rows)
    lines = [SUBJECT, 'Generated: ' + now.astimezone(LOCAL_TIMEZONE).strftime('%b %d, %Y, %I:%M %p (Asia/Karachi)'),
             '', f"Courses scanned: {result['courses_scanned']}", f'Total assignments: {len(rows)}',
             f'Submitted: {submitted}', f'Not submitted: {len(rows) - submitted}',
             f'Overdue: {overdue}', f"Needs review: {sum(bool(r['Needs review']) for r in rows)}",
             '', 'Coverage: assignments visible on each fetched current-semester course page.',
             'Pagination and assignment-detail navigation remain unverified.']
    for course in result['courses']:
        if not course['scanned']:
            raise LMSError('Report not sent: a current course was not scanned.')
        course_rows = [r for r in rows if r['Course context'].get('id') == course['course_id']
                       and r['Course'] == course['course']]
        if len(course_rows) != course['assignments']:
            raise LMSError('Report not sent: assignment counts do not match the fresh course scan.')
        lines.extend(['', clean(course['course']),
                      f"Semester: {clean(course['semester'])} | Section: {clean(course['section'])}"])
        if not course_rows:
            lines.append('No assignments found.')
        for row in course_rows:
            deadline = ('Needs review' if row['Parsed deadline'] == 'Unknown' else
                        datetime.fromisoformat(row['Parsed deadline']).astimezone(LOCAL_TIMEZONE).strftime('%b %d, %Y, %I:%M %p (Asia/Karachi)'))
            stored_status = row['Local status']
            # Keep the database workflow intact; report its extra workflow states
            # under the requested IN_PROGRESS category, retaining the exact state.
            local_status = 'IN_PROGRESS' if stored_status in {'REVIEWED', 'READY_TO_SUBMIT'} else stored_status
            submission = ('Submitted' if row['Observed submitted'] else
                          'Not Submitted' if row['Submission evidence'] == 'Explicit no-submission text' else 'Unknown')
            lines.extend(['', '- ' + clean(row['Title']), '  Course: ' + clean(row['Course'])])
            if row['Assignment number']:
                lines.append('  Assignment number: ' + clean(row['Assignment number']))
            lines.extend(['  Deadline: ' + deadline, '  Submission status: ' + submission,
                          '  Local status: ' + local_status,
                          '  Needs review: ' + ('Yes' if row['Needs review'] else 'No')])
            if stored_status != local_status:
                lines.append('  Stored workflow status: ' + stored_status)
            if (row['Deadline conflict'] or any(c.get('conditional') for c in row['Deadline sources'])
                    or row['Parsed deadline'] == 'Unknown'):
                lines.append('  Raw deadline note: ' + clean(row['Raw deadline']))
                for candidate in row['Deadline sources']:
                    lines.append('    ' + clean(candidate.get('raw')) +
                                 (' [' + ', '.join(clean(v) for v in candidate.get('labels', [])) + ']' if candidate.get('labels') else ''))
            if row['Marks'] and row['Marks'] != 'Unknown':
                lines.append('  Marks: ' + clean(row['Marks']))
            if row['Returned comments']:
                lines.append('  Returned comments: ' + clean(row['Returned comments']))
            if row['LMS URL'] and row['LMS URL'] != 'Unverified / unavailable':
                lines.append('  LMS link: ' + clean(row['LMS URL']))
    if not result['courses']:
        lines.extend(['', 'No current-semester registered courses found.'])
    return '\n'.join(lines) + '\n'


def send_assignment_report(result):
    from app.email_notifications import send_email
    body = build_report(result)
    send_email(SUBJECT, body)
    print('Current-semester assignment report sent.')


def email_assignment_report():
    from app.config import load_settings
    from app.lms.base import load_settings as load_lms_settings
    from app.lms.background import run_background
    from app.email_notifications import recipient
    from app.gmail_auth import GmailError
    if not recipient():
        print('Report not sent: export ALERT_EMAIL first (manual commands do not load config/.env).', file=sys.stderr)
        return 1
    try:
        code = run_background(load_lms_settings(), load_settings(), multi_course=True, report_email=True)
        if code:
            print('Report not sent: scan incomplete, busy, or authentication unavailable. Review scan output.', file=sys.stderr)
        return code
    except (GmailError, LMSError, OSError, ValueError) as exc:
        # Browser details and OAuth credentials never enter report error output.
        if isinstance(exc, GmailError):
            print(f'Report email: {exc}', file=sys.stderr)
        else:
            print('Report not sent: configuration or report validation failed.', file=sys.stderr)
        return 1
