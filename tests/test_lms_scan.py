"""Synthetic table snapshots only; no real portal, credentials, or notifications."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
import sqlite3
import pytest
from app.lms.extraction import HEADERS, read_assignment_table
from app.lms.base import LMSError
from app.lms.parser import parse_assignment_table, parse_lms_deadline, public_link
from app.lms.scan import scan_table
from app.lms.storage import LMSStorage, readonly_connection, match_assignment
from app.models import Source, Status
from app.notifier import NotificationError


@pytest.fixture
def table():
    text = ['2', 'Assignment 2', 'Individual work', 'Not submitted', '', '', 'Submit', 'September 22, 2026 at 11:59 PM']
    return {'headers': list(HEADERS), 'rows': [[{'text': v, 'colspan': 1, 'rowspan': 1, 'links': [], 'buttons': []} for v in text]],
            'course': 'Artificial Intelligence', 'semester': 'Fall 2026', 'page_url': 'https://lms.example.edu/assignments'}


def parse(table):
    records, errors = parse_assignment_table(table, now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    assert not errors
    return records[0]


def test_row_fields(table):
    record = parse(table)
    assert record.assignment.title == 'Assignment 2'
    assert record.assignment.source is Source.LMS
    assert record.assignment.deadline.isoformat() == '2026-09-22T18:59:00+00:00'
    assert record.assignment.needs_review
    assert record.metadata['assignment_number'] == '2'
    assert record.metadata['submission_remarks'] == 'Individual work'


def test_reordered_headings(table):
    table['headers'].reverse()
    table['rows'][0].reverse()
    assert parse(table).assignment.title == 'Assignment 2'


@pytest.mark.parametrize('value', ['', 'September 22', 'September 22, 2026', '09/10/2026 12:00'])
def test_ambiguous_deadline(value, table):
    table['rows'][0][-1]['text'] = value
    assert parse(table).assignment.deadline is None
    assert parse(table).assignment.needs_review


def test_unknown_course(table):
    table['course'] = 'Select Course'
    assert parse(table).assignment.course == 'Unknown course'


@pytest.mark.parametrize('value,expected', [('Submitted', Status.SUBMITTED), ('Not submitted', Status.NEW),
    ('Submit', Status.NEW), ('draft.docx', Status.NEW), ('Submission received', Status.SUBMITTED)])
def test_submission_evidence(table, value, expected):
    table['rows'][0][3]['text'] = value
    assert parse(table).assignment.status is expected


def test_overdue(table):
    table['rows'][0][-1]['text'] = '2026-09-01 12:00'
    assert parse(table).assignment.status is Status.OVERDUE


def test_id_not_row_number(table):
    first = parse(table).assignment.external_message_id
    table['rows'][0][0]['text'] = '99'
    assert parse(table).assignment.external_message_id == first


def test_empty_table(table):
    table['rows'] = [[{'text': 'No assignments found', 'colspan': 8}]]
    assert parse_assignment_table(table) == ([], [])


def test_malformed_row_does_not_stop_next(table):
    table['rows'].insert(0, [{'text': 'broken'}])
    records, errors = parse_assignment_table(table)
    assert len(records) == len(errors) == 1


def test_table_dom_contract(table):
    page = Mock()
    page.frames = []
    page.url = table['page_url']
    page.evaluate.return_value = {'tables': [table], 'course': table['course'], 'semester': table['semester']}
    assert read_assignment_table(page)['course'] == 'Artificial Intelligence'
    page.click.assert_not_called()
    page.evaluate.return_value = {'tables': []}
    with pytest.raises(LMSError, match='Expected one'):
        read_assignment_table(page)


def test_changed_headings_rejected(table):
    page = Mock()
    page.frames = []
    page.url = table['page_url']
    table['headers'][-1] = 'Changed'
    page.evaluate.return_value = {'tables': [table]}
    with pytest.raises(LMSError, match='headings changed'):
        read_assignment_table(page)


def test_lms_duplicate_and_metadata(database, table):
    store = LMSStorage(database.path)
    record = parse(table)
    first, new = store.import_record(record)
    second, again = store.import_record(record)
    assert new and not again and first.id == second.id
    assert len(database.list_assignments()) == 1
    with database._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM lms_records').fetchone()[0] == 1
    assert database.delete_assignment(first.id)
    with database._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM lms_records').fetchone()[0] == 0


def test_gmail_merge_preserves_metadata(database, table):
    record = parse(table)
    original = database.create_assignment(replace(record.assignment, source=Source.GMAIL, external_message_id='gmail-1',
        description='Original email metadata', status=Status.IN_PROGRESS))
    store = LMSStorage(database.path)
    updated, new = store.import_record(record)
    assert not new and updated.id == original.id
    assert updated.description == 'Original email metadata'
    assert updated.external_message_id == 'gmail-1'
    assert updated.status is Status.IN_PROGRESS
    assert len(database.list_assignments()) == 1


def test_similar_titles_not_merged(database, table):
    record = parse(table)
    database.create_assignment(replace(record.assignment, title='Assignment 20', source=Source.GMAIL, external_message_id='gmail-2'))
    _, new = LMSStorage(database.path).import_record(record)
    assert new and len(database.list_assignments()) == 2


def test_unknown_deadline_no_cross_source_match(database, table):
    record = parse(table)
    database.create_assignment(replace(record.assignment, source=Source.GMAIL, external_message_id='gmail-3'))
    with readonly_connection(database.path) as connection:
        with pytest.raises(ValueError, match='uncertain identity'):
            match_assignment(connection, replace(record.assignment, deadline=None))


def test_submitted_local_status_preserved(database, table):
    store = LMSStorage(database.path)
    first, _ = store.import_record(parse(table))
    database.mark_submitted(first.id)
    second, new = store.import_record(parse(table))
    assert second.status is Status.SUBMITTED and not new


def test_clear_lms_submission_updates_local(database, table):
    store = LMSStorage(database.path)
    first, _ = store.import_record(parse(table))
    table['rows'][0][3]['text'] = 'Submitted'
    second, _ = store.import_record(parse(table))
    assert first.id == second.id and second.status is Status.SUBMITTED


def test_notification_only_once(database, table):
    notifier = Mock()
    first = scan_table(table, database.path, notifier)
    second = scan_table(table, database.path, notifier)
    assert first['new'] == first['notified'] == 1
    assert second['existing'] == 1 and second['notified'] == 0
    notifier.send.assert_called_once()


def test_notification_failure_retries(database, table):
    notifier = Mock()
    notifier.send.side_effect = NotificationError('test')
    first = scan_table(table, database.path, notifier)
    assert first['new'] == 1 and first['errors']
    notifier.send.side_effect = None
    second = scan_table(table, database.path, notifier)
    assert second['new'] == 0 and second['notified'] == 1


def test_submitted_no_notification(database, table):
    table['rows'][0][3]['text'] = 'Submitted'
    notifier = Mock()
    assert scan_table(table, database.path, notifier)['notified'] == 0
    notifier.send.assert_not_called()


def test_dry_run_no_database_creation(tmp_path, table):
    path = tmp_path / 'absent/data.db'
    notifier = Mock()
    result = scan_table(table, path, notifier, dry_run=True, verbose=True)
    assert result['new'] == 1
    assert not path.parent.exists()
    notifier.send.assert_not_called()


def test_dry_run_old_database_unchanged(database, table):
    record = parse(table)
    database.create_assignment(replace(record.assignment, source=Source.GMAIL, external_message_id='g'))
    before = database.path.read_bytes()
    notifier = Mock()
    result = scan_table(table, database.path, notifier, dry_run=True)
    assert result['existing'] == 1
    assert database.path.read_bytes() == before
    notifier.send.assert_not_called()
    with readonly_connection(database.path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM assignments')


def test_links_not_executed_and_secrets_dropped(table):
    table['rows'][0][6]['links'] = [{'text': 'Submit', 'href': 'javascript:submit()', 'has_handler': True},
        {'text': 'Link', 'href': '/page?token=secret'}]
    record = parse(table)
    assert all(link['url'] is None for link in record.metadata['links']['Action'])
    assert record.assignment.lms_url is None


def test_migration_preserves_existing_reminders(database, table):
    record = parse(table)
    original = database.create_assignment(record.assignment)
    database.send_reminder_once(original, 'DUE_TOMORROW', lambda a: None, datetime.now(timezone.utc))
    before = database.list_reminder_events(original.id)
    store = LMSStorage(database.path)
    store.import_record(record)
    assert database.list_reminder_events(original.id) == before


def test_cli_scan_early_dispatch(monkeypatch, tmp_path):
    from app.main import main
    from app.lms.base import LMSSettings
    settings = LMSSettings('https://portal.example.edu', tmp_path / 'state.json')
    monkeypatch.setattr('app.lms.base.load_settings', lambda: settings)
    run = Mock(return_value=0)
    monkeypatch.setattr('app.lms.bahria.BahriaLMSAdapter.run', run)
    monkeypatch.setattr('app.main.Database', Mock(side_effect=AssertionError('Unexpected database initialization')))
    assert main(['lms-scan', '--single-course', '--dry-run', '--verbose']) == 0
    run.assert_called_once_with('lms-scan', dry_run=True, verbose=True)


def test_changed_fallback_deadline_not_duplicate(database, table):
    notifier = Mock()
    first = scan_table(table, database.path, notifier)
    table['rows'][0][-1]['text'] = 'September 23, 2026 at 11:59 PM'
    second = scan_table(table, database.path, notifier)
    assert first['new'] == 1 and second['errors'] and second['new'] == 0
    assert len(database.list_assignments()) == 1
    notifier.send.assert_called_once()


def test_repeated_rows_in_dry_run(table, tmp_path):
    table['rows'].append(deepcopy(table['rows'][0]))
    result = scan_table(table, tmp_path / 'absent.db', Mock(), dry_run=True)
    assert result['new'] == result['existing'] == 1


def test_unknown_spanning_message_not_empty(table):
    table['rows'] = [[{'text': 'Portal maintenance required', 'colspan': 8}]]
    records, errors = parse_assignment_table(table)
    assert not records and errors


def test_missing_metadata_does_not_erase_marks(database, table):
    import json
    store = LMSStorage(database.path)
    table['rows'][0][4]['text'] = '8 / 10'
    record = parse(table)
    store.import_record(record)
    table['rows'][0][4]['text'] = ''
    store.import_record(parse(table))
    with database._connect() as connection:
        metadata = json.loads(connection.execute('SELECT metadata FROM lms_records').fetchone()[0])
    assert metadata['marks'] == '8 / 10'
