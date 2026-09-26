from datetime import datetime
from unittest.mock import Mock

import pytest

from app.assignment_report import SUBJECT, build_report
from app.database import Database
from app.gmail_auth import GmailError
from app.lms import background, courses
from app.main import main
from app.models import LOCAL_TIMEZONE, Status
from test_lms_courses import scanner, table, course, NAMES
from test_lms_background import setup

NOW = datetime(2026, 9, 24, 12, tzinfo=LOCAL_TIMEZONE)


@pytest.fixture
def email(monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL', 'report@example.com')
    send = Mock()
    monkeypatch.setattr('app.email_notifications.send_email', send)
    return send


def test_format_all_fields_empty_course_and_summary(scanner):
    found, fetch, notifier, path, run = scanner
    first = table(NAMES[0], 'Lab 05')
    first['rows'][0][4]['text'] = '8 / 10'
    first['rows'][0][5]['text'] = 'Good work'
    second = table(NAMES[1], 'Lab 06')
    second['rows'][0][3]['text'] = 'Submitted'
    empty = table(NAMES[2]); empty['rows'] = []
    fetch.side_effect = [first, second, empty]
    result = run(notify=False)
    body = build_report(result, NOW)
    for value in (SUBJECT, 'Courses scanned: 3', 'Total assignments: 2', 'Submitted: 1',
                  'Not submitted: 1', 'Overdue: 1', 'No assignments found.',
                  'Course: Synthetic Alpha', 'Assignment number: 1', 'Marks: 8 / 10',
                  'Returned comments: Good work', 'Local status: SUBMITTED',
                  'Submission status: Not Submitted', 'Needs review: No', '(Asia/Karachi)'):
        assert value in body
    assert body.index(NAMES[0]) < body.index(NAMES[1]) < body.index(NAMES[2])
    notifier.send.assert_not_called()


def test_unresolved_deadline_conflict_and_reliable_link(scanner):
    _, _, _, _, run = scanner
    result = run(notify=False)
    row = result['reports'][0]
    row.update({'Parsed deadline': 'Unknown', 'Needs review': True,
                'Deadline conflict': True, 'Raw deadline': 'Unresolved conditional extension',
                'Deadline sources': [{'raw': '25 September', 'conditional': True,
                                      'labels': ['Extended only for missing']}],
                'LMS URL': 'https://lms.bahria.edu.pk/Student/Assignments.php'})
    body = build_report(result, NOW)
    assert 'Deadline: Needs review' in body
    assert 'Raw deadline note: Unresolved conditional extension' in body
    assert '25 September [Extended only for missing]' in body
    assert 'LMS link: https://lms.bahria.edu.pk/Student/Assignments.php' in body


def test_persisted_in_progress_not_overwritten_and_history_not_reported(scanner):
    _, _, _, path, run = scanner
    run(notify=False)
    database = Database(path)
    a = database.list_assignments()[0]
    database.update_assignment(a.id, status=Status.IN_PROGRESS)
    result = run(notify=False)
    assert 'Local status: IN_PROGRESS' in build_report(result, NOW)
    assert database.get_assignment(a.id).status is Status.IN_PROGRESS
    assert result['new'] == 0


@pytest.mark.parametrize('problem', ['error', 'skipped', 'auth', 'count'])
def test_refuses_incomplete_report(scanner, problem):
    _, _, _, _, run = scanner
    result = run(notify=False)
    if problem == 'error': result['errors'].append('parse error')
    if problem == 'skipped': result['courses_skipped'] = 1
    if problem == 'auth': result['auth_expired'] = True
    if problem == 'count': result['courses'][0]['assignments'] += 1
    with pytest.raises(background.LMSError, match='Report not sent'):
        build_report(result, NOW)


@pytest.fixture
def report_scan(setup, monkeypatch):
    settings, local, old_table, fetch, factory, notifier, clock, run = setup
    found = [course(name, 'c' + str(i)) for i, name in enumerate(NAMES)]
    monkeypatch.setattr(courses, 'discover_courses', Mock(return_value=found))
    fetch_course = Mock(side_effect=lambda context, c: table(c.name))
    monkeypatch.setattr(courses, 'fetch_course_table', fetch_course)
    return local, notifier, run, fetch_course


def test_real_shared_scan_updates_all_courses_sends_only_one_report(report_scan, email, monkeypatch):
    local, notifier, run, fetch = report_scan
    monkeypatch.setenv('LMS_COURSE_INCLUDE', 'not a registered course')
    monkeypatch.setenv('LMS_COURSE_EXCLUDE', ','.join(NAMES))
    assert run(report_email=True) == 0
    email.assert_called_once()
    assert email.call_args.args[0] == SUBJECT
    assert 'Courses scanned: 3' in email.call_args.args[1]
    assert 'Total assignments: 3' in email.call_args.args[1]
    assert fetch.call_count == 3
    notifier.send.assert_not_called()
    database = Database(local.database_path)
    assert len(database.list_assignments()) == 3
    with database._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM notification_events WHERE state='pending'").fetchone()[0] == 3
    # Another explicit report sends one report again, not event alerts or duplicates.
    assert run(report_email=True) == 0
    assert email.call_count == 2
    assert len(database.list_assignments()) == 3


def test_normal_scan_still_delivers_genuinely_new_queued_events(report_scan, email):
    local, notifier, run, _ = report_scan
    assert run(report_email=True) == 0
    email.reset_mock()
    assert run(multi_course=True) == 0
    assert email.call_count == 3
    assert notifier.send.call_count == 3
    email.reset_mock()
    assert run(multi_course=True) == 0
    email.assert_not_called()


def test_course_failure_does_not_email_partial_report(report_scan, email):
    _, notifier, run, fetch = report_scan
    fetch.side_effect = [table(NAMES[0]), courses.NoAssignmentsSection(), table(NAMES[2])]
    assert run(report_email=True) == 1
    email.assert_not_called()
    notifier.send.assert_not_called()


def test_auth_failure_report_sends_no_other_email(report_scan, email):
    _, notifier, run, fetch = report_scan
    fetch.side_effect = background.AuthExpired('private')
    assert run(report_email=True) == 1
    email.assert_not_called()
    notifier.send.assert_not_called()


def test_report_busy_does_not_claim_success(report_scan, email):
    local, _, run, _ = report_scan
    with background.scan_lock(local.data_dir):
        assert run(report_email=True) == 1
    email.assert_not_called()


def test_cli_dispatch_and_gmail_failure(monkeypatch, email, capsys):
    runner = Mock(return_value=0)
    monkeypatch.setattr(background, 'run_background', runner)
    assert main(['email-assignment-report']) == 0
    assert runner.call_args.kwargs == {'multi_course': True, 'report_email': True}
    runner.side_effect = GmailError('Gmail network request failed.')
    assert main(['email-assignment-report']) == 1
    assert 'Gmail network request failed' in capsys.readouterr().err


def test_missing_recipient_does_not_scan(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(background, 'run_background', runner)
    assert main(['email-assignment-report']) == 1
    runner.assert_not_called()


def test_report_retains_full_title_comments_and_verified_link(tmp_path):
    from dataclasses import replace
    from app.lms.parser import parse_assignment_table
    from app.lms.scan import scan_records
    value = table(title='A long assignment title ' * 12)
    value['rows'][0][5]['text'] = 'Detailed feedback ' * 20
    records, errors = parse_assignment_table(value)
    link = 'https://lms.bahria.edu.pk/Student/Assignments.php'
    records[0] = replace(records[0], assignment=replace(records[0].assignment, lms_url=link))
    result = scan_records(records, errors, tmp_path / 'report.db', Mock(),
                          notify=False, report_details=True)
    row = result['reports'][0]
    assert row['Title'] == value['rows'][0][1]['text'].strip()
    assert row['Returned comments'] == value['rows'][0][5]['text']
    assert row['LMS URL'] == link
