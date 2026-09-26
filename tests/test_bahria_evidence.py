"""Observed date/cell shapes with synthetic filenames and download envelopes."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
from urllib.parse import urlencode
import pytest
from app.lms.evidence import download_evidence, submission_evidence, action_evidence
from app.lms.extraction import HEADERS
from app.lms.parser import parse_lms_deadline, parse_assignment_table, public_link
from app.lms.scan import scan_table
from app.models import LOCAL_TIMEZONE, Status

BASE = 'https://lms.example.edu/Student/Assignments.php'


def link(category='AssignmentSubmissions', filename='synthetic-report.pdf', date='2026-09-18'):
    envelope = f'12345,{date},{category},0,{filename},lms.example.edu/Student/Assignments.php'
    key = base64.b64encode(envelope.encode()).decode()
    return '/Student/Download.php?' + urlencode({'k': key})


def table(deadline='16 September 2026-09:00 am', submission='No Submission'):
    values = ['2', 'Synthetic Lab 02', '', submission, 'Not marked yet', '', 'Deadline Exceeded', deadline]
    cells = [{'text': v, 'raw_text': v, 'links': [], 'buttons': [], 'forms': []} for v in values]
    return {'headers': list(HEADERS), 'rows': [cells], 'course': 'Synthetic course', 'page_url': BASE}


def record(snapshot):
    records, errors = parse_assignment_table(snapshot, now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    assert not errors
    return records[0]


@pytest.mark.parametrize('raw,hour,minute,day', [
    ('16 September 2026-09:00 am', 9, 0, 16),
    ('9 September 2026 - 11:55 pm', 23, 55, 9),
    ('9 September 2026-09:00 am', 9, 0, 9),
])
def test_exact_observed_deadlines(raw, hour, minute, day):
    parsed = parse_lms_deadline(raw)
    assert parsed == datetime(2026, 9, day, hour, minute, tzinfo=LOCAL_TIMEZONE)
    assert parsed.tzinfo == LOCAL_TIMEZONE


@pytest.mark.parametrize('raw', ['', '31 September 2026-09:00 am', '16 September 2026-13:00 pm',
    '16 September 2026', '16 September 2026-09:70 am', 'deadline unknown',
    '9 September 2026 - 11:55 pm\n9 September 2026-09:00 am'])
def test_invalid_or_ambiguous_keeps_raw(raw):
    parsed = record(table(deadline=raw))
    assert parsed.assignment.deadline is None
    assert parsed.assignment.needs_review
    assert parsed.metadata['deadline_text'] == raw


def test_no_submission_not_submitted():
    parsed = record(table())
    assert parsed.assignment.status is Status.OVERDUE
    assert parsed.metadata['submission_evidence'] == 'Explicit no-submission text'


def test_submission_alone_insufficient():
    parsed = record(table(submission='Submission'))
    assert parsed.assignment.status is not Status.SUBMITTED
    assert 'insufficient' in parsed.metadata['submission_evidence']


def test_explicit_submission_file_submitted():
    snapshot = table(submission='Submission')
    snapshot['rows'][0][3]['links'] = [{'href': link(), 'text': 'Submission'}]
    parsed = record(snapshot)
    assert parsed.assignment.status is Status.SUBMITTED
    assert 'verified AssignmentSubmissions' in parsed.metadata['submission_evidence']
    assert parsed.metadata['links']['Added Submission'][0]['url'] is None


def test_assignment_instruction_file_is_not_submission():
    snapshot = table(submission='Submission')
    snapshot['rows'][0][3]['links'] = [{'href': link(category='Assignment'), 'text': 'Submission'}]
    assert record(snapshot).assignment.status is not Status.SUBMITTED


def test_negative_status_conflict_requires_review():
    snapshot = table()
    snapshot['rows'][0][3]['links'] = [{'href': link(), 'text': 'Submission'}]
    parsed = record(snapshot)
    assert parsed.assignment.status is not Status.SUBMITTED
    assert 'conflicting' in parsed.metadata['submission_evidence']


def test_instruction_attachment_extracted_without_opaque_key():
    snapshot = table()
    snapshot['rows'][0][2]['links'] = [{'href': link('Assignment', 'instructions.pdf'), 'text': 'Assignment'}]
    parsed = record(snapshot)
    assert parsed.metadata['attachments'][0]['filename'] == 'instructions.pdf'
    assert parsed.metadata['attachments'][0]['endpoint'].endswith('/Student/Download.php')
    assert '?k=' not in str(parsed.metadata)


@pytest.mark.parametrize('value', ['javascript:submit()', '/Student/Download.php?k=bad',
    'https://other.example.edu/Student/Download.php?k=bad'])
def test_unverified_download_rejected(value):
    assert download_evidence(value, BASE) is None


def test_malformed_download_file_rejected():
    assert download_evidence(link(filename='../private.txt'), BASE) is None


def test_action_text_no_detail_url_or_id():
    action = action_evidence({'text': 'Deadline Exceeded', 'links': [], 'buttons': []}, BASE)
    assert action == {'type': 'text', 'url': None, 'id': None, 'verified': True}
    parsed = record(table())
    assert parsed.assignment.lms_url is None


@pytest.mark.parametrize('cell', [None, {'links': 'wrong'}, {'links': [None]}])
def test_malformed_action(cell):
    assert action_evidence(cell, BASE)['type'] == 'malformed'


def test_unverified_action_url_not_promoted_to_detail():
    result = action_evidence({'links': [{'href': '/unverified', 'text': 'Action'}]}, BASE)
    assert result['url'] is None and not result['verified']


def test_javascript_and_form_actions_not_executed():
    assert action_evidence({'links': [{'href': 'javascript:submit()'}]}, BASE)['type'].startswith('JavaScript')
    assert action_evidence({'forms': [{'method': 'post'}]}, BASE)['type'].startswith('form')


def test_fallback_stable_across_changing_download_envelope():
    snapshot = table()
    snapshot['rows'][0][2]['links'] = [{'href': link('Assignment', 'instructions.pdf')}]
    first = record(snapshot).assignment.external_message_id
    snapshot['rows'][0][0]['text'] = '99'
    snapshot['rows'][0][2]['links'] = [{'href': link('Assignment', 'instructions.pdf', date='2026-09-19')}]
    assert record(snapshot).assignment.external_message_id == first
    assert first.startswith('bahria:fallback:')
    snapshot['rows'][0][1]['text'] = 'Different assignment'
    assert record(snapshot).assignment.external_message_id != first


def test_opaque_parameters_never_public():
    assert public_link(link(), BASE) is None


def test_verbose_requested_fields_and_readonly(tmp_path, capsys):
    path = tmp_path / 'missing.db'
    notifier = Mock()
    result = scan_table(table(), path, notifier, dry_run=True, verbose=True)
    report = result['reports'][0]
    assert {'Title', 'Raw deadline', 'Parsed deadline', 'Action type', 'Action URL/ID', 'External ID',
            'Submission evidence', 'Local status', 'Needs review'} <= report.keys()
    assert report['Parsed deadline'] == '2026-09-16T09:00:00+05:00'
    assert not path.exists()
    notifier.send.assert_not_called()


def sources(snapshot, entries):
    snapshot['rows'][0][-1]['deadline_sources'] = entries
    return snapshot


def candidate(raw, label='', hidden=False):
    return {'raw': raw, 'hidden': hidden, 'context': [{'tag': 'small', 'title': label, 'label': ''}]}


def test_real_actual_and_conditional_extension():
    snapshot = table(deadline='9 September 2026 - 11:55 pm\n9 September 2026-09:00 am')
    sources(snapshot, [candidate('9 September 2026 - 11:55 pm', 'Extended only for missing'),
                       candidate('9 September 2026-09:00 am', 'Actual')])
    parsed = record(snapshot)
    assert parsed.assignment.deadline.astimezone(LOCAL_TIMEZONE) == datetime(2026, 9, 9, 9, tzinfo=LOCAL_TIMEZONE)
    assert parsed.metadata['deadline_conflict']
    assert parsed.metadata['deadline_conditional_extension']
    assert parsed.assignment.status is Status.OVERDUE
    assert parsed.assignment.needs_review


def test_conflict_unlabeled_refused():
    snapshot = table(deadline='9 September 2026 - 11:55 pm\n9 September 2026-09:00 am')
    parsed = record(snapshot)
    assert parsed.assignment.deadline is None and parsed.metadata['deadline_conflict']
    assert 'refused' in parsed.metadata['deadline_selection_reason']


def test_explicit_deadline_selected_independent_of_order():
    snapshot = table()
    entries = [candidate('9 September 2026 - 11:55 pm', 'Deadline'), candidate('9 September 2026-09:00 am', 'Opens')]
    sources(snapshot, entries)
    first = record(snapshot)
    sources(snapshot, list(reversed(entries)))
    second = record(snapshot)
    assert first.assignment.deadline == second.assignment.deadline
    assert first.assignment.deadline.astimezone(LOCAL_TIMEZONE).hour == 23
    assert not first.assignment.needs_review


def test_hidden_duplicate_ignored():
    snapshot = table()
    sources(snapshot, [candidate('16 September 2026-09:00 am'),
                       candidate('9 September 2026-09:00 am', 'Actual', hidden=True)])
    parsed = record(snapshot)
    assert parsed.assignment.deadline.astimezone(LOCAL_TIMEZONE).day == 16
    assert not parsed.metadata['deadline_conflict']
    assert len(parsed.metadata['deadline_raw_values']) == 2


def test_ambiguous_identity_not_affected_by_candidates_or_order():
    snapshot = table(deadline='9 September 2026 - 11:55 pm\n9 September 2026-09:00 am')
    first = record(snapshot).assignment.external_message_id
    snapshot['rows'][0][-1]['raw_text'] = '10 September 2026-09:00 am\n12 September 2026-10:00 am'
    assert record(snapshot).assignment.external_message_id == first
    snapshot['rows'][0][0]['text'] = '3'
    assert record(snapshot).assignment.external_message_id != first
    assert first.startswith('bahria:unresolved:')


def test_complete_submitted_assignment_not_review_default():
    snapshot = table(submission='Submission')
    snapshot['rows'][0][3]['links'] = [{'href': link(), 'text': 'Submission'}]
    sources(snapshot, [candidate('16 September 2026-09:00 am', 'Actual')])
    parsed = record(snapshot)
    assert parsed.assignment.status is Status.SUBMITTED
    assert not parsed.assignment.needs_review
    assert parsed.metadata['review_reasons'] == []


def test_invalid_explicit_deadline_does_not_choose_secondary():
    snapshot = table()
    sources(snapshot, [candidate('31 September 2026-09:00 am', 'Deadline'), candidate('16 September 2026-09:00 am')])
    assert record(snapshot).assignment.deadline is None


def test_resolved_lms_review_flag_can_clear(database):
    from app.lms.storage import LMSStorage
    from dataclasses import replace
    snapshot = table(submission='Submission')
    snapshot['rows'][0][3]['links'] = [{'href': link(), 'text': 'Submission'}]
    incoming = record(snapshot)
    old = database.create_assignment(replace(incoming.assignment, needs_review=True))
    updated, created = LMSStorage(database.path).import_record(incoming)
    assert updated.id == old.id and not created
    assert not updated.needs_review


def test_conditional_extension_alone_is_not_actual_deadline():
    snapshot = table(deadline='9 September 2026 - 11:55 pm')
    sources(snapshot, [candidate('9 September 2026 - 11:55 pm', 'Extended only for missing')])
    parsed = record(snapshot)
    assert parsed.assignment.deadline is None
    assert parsed.assignment.needs_review
    assert parsed.metadata['deadline_conditional_extension']
    assert parsed.metadata['deadline_raw_values'] == ['9 September 2026 - 11:55 pm']


def test_conflicting_explicit_labels_remain_unknown():
    snapshot = table()
    sources(snapshot, [candidate('9 September 2026 - 11:55 pm', 'Deadline'),
                       candidate('9 September 2026-09:00 am', 'Actual')])
    parsed = record(snapshot)
    assert parsed.assignment.deadline is None
    assert parsed.metadata['deadline_conflict']
    assert parsed.assignment.needs_review


def test_review_reasons_clear_in_stored_metadata(database):
    import json
    from app.lms.storage import LMSStorage
    snapshot = table(submission='Submission')
    store = LMSStorage(database.path)
    initial, created = store.import_record(record(snapshot))
    assert created and initial.needs_review
    snapshot['rows'][0][3]['links'] = [{'href': link(), 'text': 'Submission'}]
    updated, created = store.import_record(record(snapshot))
    assert not created and not updated.needs_review
    with database._connect() as connection:
        metadata = json.loads(connection.execute('SELECT metadata FROM lms_records').fetchone()[0])
    assert metadata['review_reasons'] == []
