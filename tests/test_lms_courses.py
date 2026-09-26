"""Verified DOM shapes with synthetic courses and mocked Playwright pages only."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import json

import pytest

from app.database import Database
from app.lms import courses
from app.lms.base import LMSError, LMSSettings
from app.lms.extraction import HEADERS
from app.lms.parser import parse_assignment_table
from app.lms.storage import LMSStorage
from app.models import Source

ROOT = 'https://lms.bahria.edu.pk/Student/'
TARGET = {'url': ROOT + 'Assignments.php', 'course': 'Synthetic Alpha', 'semester': 'Fall-2026'}
NAMES = ['Synthetic Alpha', 'Synthetic Beta', 'Synthetic Gamma']


def option(text, value, selected=False):
    return dict(text=text, value=value, selected=selected, disabled=False)


def registered(name, section='Class A', term='Fall-2026'):
    return [{'text': text, 'colspan': 1} for text in ('1', name, section, term)]


def snapshot():
    return {
        'semester': {'value': 'fall', 'handler': 'SelectSemester()', 'options': [
            option('Spring-2025', 'old'), option('Fall-2026', 'fall', True), option('Summer-2026', 'summer')]},
        'course': {'value': 'c0', 'handler': 'GetCourses()', 'options': [option('Select Course', '')] +
                   [option(name, 'c' + str(i)) for i, name in enumerate(NAMES)]},
        'registrations': [{'headers': list(courses.REGISTERED_HEADERS),
                           'rows': [registered(name) for name in NAMES]}],
    }


def table(name='Synthetic Alpha', title='Lab exercise'):
    texts = ['1', title, '', 'No Submission', '', '', 'Deadline Exceeded', '23 September 2026-09:00 am']
    return {'headers': list(HEADERS), 'rows': [[{'text': text} for text in texts]],
            'course': name, 'semester': 'Fall-2026', 'page_url': ROOT + 'Assignments.php'}


def course(name='Synthetic Alpha', value='c0'):
    return courses.Course(name, 'Class A', 'Fall-2026', 'fall', selector_value=value,
                          assignments_url=ROOT + 'Assignments.php')


def mocked_page(data=None):
    data = deepcopy(data or snapshot())
    page = MagicMock()
    page.url = TARGET['url']
    page.frames = []
    page.get_by_role.return_value.count.return_value = 0
    def goto(url, **kwargs):
        page.url = url
        return SimpleNamespace(status=200)
    page.goto.side_effect = goto
    def evaluate(script):
        if script == courses.COURSE_SCRIPT:
            return deepcopy(data)
        selected = next(o['text'] for o in data['course']['options'] if o['value'] == data['course']['value'])
        return {'tables': [table(selected)], 'course': selected, 'semester': 'Fall-2026'}
    page.evaluate.side_effect = evaluate
    def filter_links(**kwargs):
        label = 'Dashboard' if 'Dashboard' in kwargs['has_text'].pattern else 'Assignments'
        anchor = Mock()
        anchor.is_visible.return_value = True
        anchor.get_attribute.side_effect = lambda key: label + '.php' if key == 'href' else None
        matches = Mock()
        matches.count.return_value = 1
        matches.nth.return_value = anchor
        return matches
    anchors = Mock()
    anchors.filter.side_effect = filter_links
    def locator(selector):
        if selector == 'a':
            return anchors
        field = 'semester' if selector == 'select#semesterId' else 'course'
        result = Mock()
        result.select_option.side_effect = lambda value: data[field].update(value=value)
        return result
    page.locator.side_effect = locator
    page.expect_navigation.return_value.__enter__.return_value.value = SimpleNamespace(status=200)
    return page, data


def test_discover_multiple_courses_from_mocked_page():
    page, _ = mocked_page()
    context = Mock()
    context.new_page.return_value = page
    found = courses.discover_courses(context, TARGET)
    assert [c.name for c in found] == NAMES
    assert all(c.stable_id and c.assignments_url == ROOT + 'Assignments.php' for c in found)
    assert all(c.course_url is None and not c.code for c in found)
    assert all(c.name not in ('Dashboard', 'Logout', 'Profile') for c in found)
    page.close.assert_called_once()
    assert [call.args[0] for call in page.goto.call_args_list] == [TARGET['url'], ROOT + 'Dashboard.php', ROOT + 'Assignments.php']


def test_current_semester_not_historical_selection_or_option_order():
    data = snapshot()
    data['semester']['value'] = 'old'
    data['semester']['options'].reverse()
    assert courses.current_semester(data)['value'] == 'fall'
    data['registrations'][0]['rows'].append(registered('Historical course', term='Spring-2025'))
    assert len(courses.registered_rows(data, courses.current_semester(data))) == 3
    page, state = mocked_page(data)
    context = Mock(new_page=Mock(return_value=page))
    found = courses.discover_courses(context, TARGET)
    assert state['semester']['value'] == 'fall'
    assert len(found) == 3


def test_unrecognized_semester_does_not_traverse_archive():
    data = snapshot()
    data['semester']['options'].append(option('Something else', 'x'))
    with pytest.raises(LMSError, match='semester'):
        courses.current_semester(data)


def test_same_name_sections_are_not_arbitrarily_associated():
    data = snapshot()
    data['registrations'][0]['rows'].append(registered(NAMES[0], section='Class B'))
    page, _ = mocked_page(data)
    found = courses.discover_courses(Mock(new_page=Mock(return_value=page)), TARGET)
    assert len(found) == 4
    assert all(c.assignments_url is None for c in found if c.name == NAMES[0])


def test_fetch_uses_real_selector_and_verified_context():
    page, state = mocked_page()
    result = courses.fetch_course_table(Mock(new_page=Mock(return_value=page)), course(NAMES[1], 'c1'))
    assert state['course']['value'] == 'c1'
    assert result['course'] == NAMES[1]
    assert result['course_context']['id'] == course(NAMES[1], 'c1').stable_id
    page.expect_navigation.assert_called_once()
    page.close.assert_called_once()


def test_changed_course_handler_stops_safely():
    data = snapshot()
    data['course']['handler'] = 'submitAssignment()'
    page, _ = mocked_page(data)
    with pytest.raises(LMSError, match='structure changed'):
        courses.fetch_course_table(Mock(new_page=Mock(return_value=page)), course(NAMES[1], 'c1'))
    page.expect_navigation.assert_not_called()


@pytest.mark.parametrize('include,exclude,expected', [
    ('', '', True), (' SYNTHETIC ALPHA, Other ', '', True), ('Other', '', False),
    ('Synthetic Alpha', 'synthetic alpha', False), ('CS101', '', True), ('', 'CS101', False),
])
def test_filters_exact_names_or_codes(include, exclude, expected):
    assert courses.course_filter(replace(course(), code='CS101'), include, exclude) is expected


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    found = [course(name, 'c' + str(i)) for i, name in enumerate(NAMES)]
    monkeypatch.setattr(courses, 'discover_courses', Mock(return_value=found))
    fetch = Mock(side_effect=lambda context, c: table(c.name))
    monkeypatch.setattr(courses, 'fetch_course_table', fetch)
    notifier = Mock()
    path = tmp_path / 'data.sqlite'
    def run(**kwargs):
        return courses.scan_courses(Mock(), TARGET, path, notifier, **kwargs)
    return found, fetch, notifier, path, run


def test_multi_course_dedup_and_notifications_once(scanner):
    found, fetch, notifier, path, run = scanner
    first = run()
    second = run()
    assert first['new'] == first['notified'] == 3
    assert second['new'] == second['notified'] == second['updated'] == 0
    assert second['existing'] == 3
    assert len(Database(path).list_assignments()) == 3
    assert {a.course for a in Database(path).list_assignments()} == set(NAMES)
    assert notifier.send.call_count == 3


def test_metadata_update_does_not_create_or_renotify(scanner):
    found, fetch, notifier, path, run = scanner
    run()
    def updated(context, c):
        value = table(c.name)
        value['rows'][0][3]['text'] = 'Submitted'
        value['rows'][0][4]['text'] = '10 / 10'
        return value
    fetch.side_effect = updated
    result = run()
    assert result['updated'] == result['submitted'] == 3
    assert result['new'] == result['notified'] == 0
    assert notifier.send.call_count == 3


def test_dry_run_no_creation_or_notifications(scanner):
    _, _, notifier, path, run = scanner
    result = run(dry_run=True, verbose=True)
    assert result['new'] == 3
    assert not path.exists()
    notifier.send.assert_not_called()


def test_dry_run_existing_database_bytes_unchanged(scanner):
    _, _, notifier, path, run = scanner
    run()
    notifier.reset_mock()
    before = path.read_bytes()
    result = run(dry_run=True)
    assert result['existing'] == 3
    assert path.read_bytes() == before
    notifier.send.assert_not_called()


def test_course_without_assignments_continues(scanner):
    _, fetch, notifier, path, run = scanner
    empty = table(NAMES[1]); empty['rows'] = [[{'text': 'No assignments found', 'colspan': 8}]]
    fetch.side_effect = [table(NAMES[0]), empty, table(NAMES[2])]
    result = run(dry_run=True)
    assert result['courses_scanned'] == 3 and result['parsed'] == 2
    assert result['courses'][1]['reason'] == 'No assignments'


def test_one_course_failure_does_not_stop_others(scanner, capsys):
    from playwright.sync_api import Error
    _, fetch, notifier, path, run = scanner
    fetch.side_effect = [table(NAMES[0]), Error('token=SECRET'), table(NAMES[2])]
    result = run(dry_run=True)
    assert result['courses_scanned'] == 2 and result['courses_skipped'] == 1
    assert result['parsed'] == 2 and len(result['errors']) == 1
    assert fetch.call_count == 3
    assert 'SECRET' not in capsys.readouterr().out


def test_no_assignment_section_is_skipped(scanner):
    _, fetch, notifier, path, run = scanner
    fetch.side_effect = [courses.NoAssignmentsSection(), table(NAMES[1]), table(NAMES[2])]
    result = run(dry_run=True)
    assert result['courses_skipped'] == 1 and result['courses_scanned'] == 2
    assert not result['errors']


def test_auth_expiry_stops_remaining_requests(scanner):
    _, fetch, notifier, path, run = scanner
    fetch.side_effect = [table(NAMES[0]), courses.AuthExpired('private')]
    result = run(dry_run=True)
    assert result['auth_expired']
    assert result['courses_scanned'] == 1 and result['courses_skipped'] == 2
    assert fetch.call_count == 2
    notifier.send.assert_not_called()


def test_filters_skip_without_navigation(scanner):
    _, fetch, _, _, run = scanner
    result = run(dry_run=True, include=NAMES[0], exclude=NAMES[1])
    assert result['courses_discovered'] == 3
    assert result['courses_scanned'] == 1 and result['courses_skipped'] == 2
    fetch.assert_called_once()


def seed_unknown(path, *, name='Unknown course', title='Unique legacy task', with_id=False):
    data = table(name, title)
    records, _ = parse_assignment_table(data)
    record = records[0]
    if with_id:
        record.metadata['course_context'] = course().metadata()
    saved, _ = LMSStorage(path).import_record(record)
    LMSStorage(path).notify_pending(lambda _: None)
    return saved


def test_unknown_course_safely_enriched_without_duplicate(scanner):
    _, fetch, notifier, path, run = scanner
    old = seed_unknown(path)
    fetch.side_effect = lambda context, c: table(c.name, 'Unique legacy task' if c.name == NAMES[0] else 'Different task ' + c.name)
    result = run()
    db = Database(path)
    assert len(db.list_assignments()) == 3
    assert db.get_assignment(old.id).course == NAMES[0]
    assert result['existing'] == result['updated'] == 1
    assert result['new'] == result['notified'] == 2
    with db._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM lms_records WHERE assignment_id=?', (old.id,)).fetchone()[0] == 2


def test_unknown_course_ambiguous_across_courses_not_merged(scanner):
    _, fetch, notifier, path, run = scanner
    old = seed_unknown(path, title='Lab exercise')
    result = run()
    assert len(result['errors']) == 3
    assert result['new'] == 0
    assert Database(path).get_assignment(old.id).course == 'Unknown course'
    notifier.send.assert_not_called()


def test_unknown_course_filtered_survey_cannot_claim_uniqueness(scanner):
    _, fetch, notifier, path, run = scanner
    old = seed_unknown(path, title='Lab exercise')
    result = run(include=NAMES[0])
    assert result['errors'] and result['new'] == 0
    assert len(Database(path).list_assignments()) == 1


def test_unknown_course_explicit_id_can_enrich_with_filter(scanner):
    _, fetch, notifier, path, run = scanner
    old = seed_unknown(path, title='Lab exercise', with_id=True)
    result = run(include=NAMES[0])
    assert not result['errors'] and result['new'] == 0
    assert Database(path).get_assignment(old.id).course == NAMES[0]
    notifier.send.assert_not_called()


def test_exact_gmail_match_still_preserved(scanner):
    _, _, notifier, path, run = scanner
    records, _ = parse_assignment_table(table())
    db = Database(path)
    old = db.create_assignment(replace(records[0].assignment, source=Source.GMAIL, external_message_id='gmail-message'))
    result = run()
    assert result['existing'] == 1 and result['new'] == 2
    assert db.get_assignment(old.id).source is Source.GMAIL


def test_discovery_only_does_not_fetch_assignments_or_open_db(scanner):
    _, fetch, notifier, path, run = scanner
    result = run(discover_only=True, dry_run=True, verbose=True)
    assert result['courses_discovered'] == 3
    fetch.assert_not_called()
    assert not path.exists()
    notifier.send.assert_not_called()


@pytest.mark.parametrize('argv,discovery', [(['lms-courses', '--dry-run', '--verbose'], True),
                                          (['lms-scan', '--dry-run', '--verbose'], False)])
def test_cli_multi_course_readonly_dispatch(tmp_path, monkeypatch, argv, discovery):
    from app.main import main
    settings = LMSSettings(ROOT, tmp_path / 'state.json')
    monkeypatch.setattr('app.lms.base.load_settings', lambda: settings)
    run = Mock(return_value=0)
    monkeypatch.setattr('app.lms.background.run_background', run)
    monkeypatch.setattr('app.main.Database', Mock(side_effect=AssertionError('CLI must not initialize DB')))
    assert main(argv) == 0
    kwargs = run.call_args.kwargs
    assert kwargs['dry_run'] and kwargs['multi_course'] and kwargs['verbose']
    assert kwargs.get('discover_only', False) is discovery


@pytest.mark.parametrize('dry_run', [False, True])
@pytest.mark.parametrize('single_course', [False, True])
def test_background_defaults_to_current_courses(tmp_path, monkeypatch, dry_run, single_course):
    from app.main import main
    settings = LMSSettings(ROOT, tmp_path / 'state.json')
    monkeypatch.setattr('app.lms.base.load_settings', lambda: settings)
    monkeypatch.setenv('ASSIGNMENT_LMS_BACKGROUND', '1')
    run = Mock(return_value=0)
    monkeypatch.setattr('app.lms.background.run_background', run)
    argv = ['lms-scan']
    if dry_run:
        argv.append('--dry-run')
    if single_course:
        argv.append('--single-course')
    assert main(argv) == 0
    assert run.call_args.kwargs['multi_course'] is (not single_course)
    assert run.call_args.kwargs['dry_run'] is dry_run
