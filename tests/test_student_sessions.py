"""Student authentication lifecycle with private synthetic state and no network."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from app.config import Settings
from app.database import Database
from app.lms import background
from app.lms.base import LMSSettings, LMSError
from app.lms.session import read_state, save_state
from app.students import add_student, get_student, student_session_path


STATE = {'cookies': [], 'origins': []}
RESULT = dict(parsed=0, new=0, existing=0, updated=0, review=0, errors=[], notified=0)


@pytest.fixture
def students(tmp_path, monkeypatch):
    local = Settings(tmp_path / 'data', tmp_path / 'logs')
    database = Database(local.database_path)
    # Model the existing deployed audit migration, without touching real data.
    with database._connect() as connection:
        for column in ('created_at', 'recipient_email', 'gmail_message_id', 'error_type', 'error_message'):
            connection.execute(f'ALTER TABLE notification_events ADD COLUMN {column} TEXT')
    configurations = {}
    for name in ('shahzad', 'danyal', 'muhammad-umar', 'talib'):
        add_student(database, name, f'{name}@example.edu', name)
        path = tmp_path / 'credentials' / 'students' / name / 'bahria_storage_state.json'
        settings = LMSSettings('https://cms.bahria.edu.pk/', path, path.with_name('browser-profile'))
        save_state(path, STATE)
        save_state(path.with_name('bahria_portal.json'), {'portal_url': settings.portal_url})
        save_state(background.target_path(settings), {
            'url': 'https://lms.bahria.edu.pk/Student/Assignments.php',
            'course': 'Synthetic course', 'semester': 'Fall 2026',
        })
        configurations[name] = settings
    factory = MagicMock()
    context = factory.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
    context.storage_state.return_value = STATE
    context.cookies.return_value = []
    scan = Mock(return_value=RESULT.copy())
    monkeypatch.setattr('app.lms.courses.scan_courses', scan)
    recovery = Mock(return_value=False)
    monkeypatch.setattr(background, 'recover_session', recovery)
    sender = Mock(return_value='synthetic-message')
    monkeypatch.setattr('app.email_notifications.send_email', sender)
    notifier = Mock()
    now = [1_900_000_000.0]

    def run(name='shahzad', **kwargs):
        return background.run_background(
            configurations[name], local, student_id=name, multi_course=True,
            playwright_factory=factory, notifier=notifier, clock=lambda: now[0], **kwargs,
        )

    return dict(local=local, database=database, configurations=configurations,
                factory=factory, context=context, scan=scan, recovery=recovery,
                sender=sender, notifier=notifier, now=now, run=run)


def test_valid_student_session_migrates_once_and_remains_isolated(students):
    s = students
    old = s['configurations']['shahzad'].state_path.read_bytes()
    assert s['run']() == 0
    assert s['run']() == 0
    s['context'].set_storage_state.assert_called_once_with(STATE)
    assert s['context'].close.call_count == 2
    launch = s['factory'].return_value.__enter__.return_value.chromium.launch_persistent_context
    assert launch.call_args.kwargs['user_data_dir'] == str(s['configurations']['shahzad'].profile_path)
    assert launch.call_args.kwargs['headless'] is True
    assert launch.call_args.kwargs['chromium_sandbox'] is True
    assert s['configurations']['shahzad'].state_path.read_bytes() == old
    assert not s['configurations']['danyal'].profile_path.exists()
    assert get_student(s['database'], 'shahzad').auth_status == 'ACTIVE'
    assert get_student(s['database'], 'danyal').auth_status == 'UNKNOWN'
    s['sender'].assert_not_called()


def test_expired_student_notifies_own_email_and_suppresses_duplicates(students, capsys, caplog):
    s = students
    s['scan'].side_effect = background.AuthExpired('cookie=SECRET password=SECRET token=SECRET')
    assert s['run']() == 1
    assert s['run']() == 1
    assert s['scan'].call_count == 1
    assert s['recovery'].call_count == 1
    s['sender'].assert_called_once()
    subject, body, recipient = s['sender'].call_args.args
    assert recipient == 'shahzad@example.edu'
    assert 'student-auth shahzad' in body
    assert 'Bahria LMS Login Required' == subject
    assert get_student(s['database'], 'shahzad').auth_status == 'LOGIN_REQUIRED'
    assert get_student(s['database'], 'danyal').auth_status == 'UNKNOWN'
    notice = background.auth_notice_path(s['local'], 'shahzad')
    assert notice.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr()
    assert 'authentication_required_cooldown' in output.out
    assert 'SECRET' not in output.out + output.err + caplog.text
    assert 'fingerprint' not in output.out
    s['now'][0] += background.AUTH_COOLDOWN
    assert s['run']() == 1
    assert s['sender'].call_count == 2


def test_email_cooldown_survives_scan_notice_reset(students):
    s = students
    s['scan'].side_effect = background.AuthExpired()
    assert s['run']() == 1
    background.clear_auth_notice(s['local'], 'shahzad')
    assert s['run']() == 1
    assert s['scan'].call_count == 2
    s['sender'].assert_called_once()


def test_missing_session_routes_through_student_notification(students):
    s = students
    s['configurations']['shahzad'].state_path.unlink()  # Synthetic fixture only.
    assert s['run']() == 1
    s['factory'].assert_not_called()
    assert s['sender'].call_args.args[2] == 'shahzad@example.edu'


def test_profile_works_without_json_snapshot(students):
    s = students
    assert s['run']() == 0
    s['configurations']['shahzad'].state_path.unlink()  # Synthetic fixture only.
    assert s['run']() == 0
    assert s['configurations']['shahzad'].state_path.exists()


def test_cms_recovery_avoids_login_notice(students):
    s = students
    s['scan'].side_effect = [background.AuthExpired(), RESULT.copy()]
    s['recovery'].return_value = True
    assert s['run']() == 0
    assert s['scan'].call_count == 2
    s['recovery'].assert_called_once()
    s['sender'].assert_not_called()
    assert get_student(s['database'], 'shahzad').auth_status == 'ACTIVE'


def test_recovery_is_bounded_even_if_lms_rejects_handoff(students):
    s = students
    s['scan'].side_effect = background.AuthExpired()
    s['recovery'].return_value = True
    assert s['run']() == 1
    assert s['scan'].call_count == 2
    s['recovery'].assert_called_once()
    s['sender'].assert_called_once()


def test_dry_run_does_not_update_database_or_cooldown(students):
    s = students
    s['scan'].side_effect = background.AuthExpired()
    assert s['run'](dry_run=True) == 1
    assert get_student(s['database'], 'shahzad').auth_status == 'UNKNOWN'
    assert not background.auth_notice_path(s['local'], 'shahzad').exists()
    s['sender'].assert_not_called()


def wire_cli(students, monkeypatch):
    # Keep CLI resolution inside the test root; registry paths remain realistic.
    root = students['local'].data_dir.parent
    monkeypatch.setattr('app.main.__file__', str(root / 'app' / 'main.py'))
    monkeypatch.setattr('app.main.load_settings', lambda: students['local'])


@pytest.mark.parametrize('result', [0, 1])
def test_student_auth_explicitly_clears_only_own_cooldown(students, monkeypatch, result):
    from app.main import main
    s = students
    s['scan'].side_effect = background.AuthExpired()
    assert s['run']() == 1
    assert s['run']('danyal') == 1
    other = background.auth_notice_path(s['local'], 'danyal').read_bytes()
    before = s['configurations']['shahzad'].state_path.read_bytes()
    wire_cli(s, monkeypatch)
    real_runner = background.run_background
    monkeypatch.setattr(background, 'run_background', lambda settings, local, **kwargs:
                        real_runner(settings, local, playwright_factory=s['factory'],
                                    notifier=s['notifier'], clock=lambda: s['now'][0], **kwargs))
    # Deliberately leave snapshot bytes identical to reproduce the original bug.
    monkeypatch.setattr('app.lms.bahria.BahriaLMSAdapter.run', Mock(return_value=result))
    assert main(['student-auth', 'shahzad']) == result
    assert s['configurations']['shahzad'].state_path.read_bytes() == before
    assert background.auth_notice_path(s['local'], 'danyal').read_bytes() == other
    s['scan'].side_effect = None
    s['scan'].reset_mock()
    if result == 0:
        assert json.loads(background.auth_notice_path(s['local'], 'shahzad').read_text()) == {}
        assert get_student(s['database'], 'shahzad').auth_status == 'ACTIVE'
        assert main(['student-scan', 'shahzad']) == 0
        s['scan'].assert_called_once()
    else:
        assert main(['student-scan', 'shahzad']) == 1
        s['scan'].assert_not_called()


def test_scan_all_mixed_sessions_continues(students, monkeypatch, capsys):
    from app.main import main
    s = students
    wire_cli(s, monkeypatch)
    real_runner = background.run_background
    monkeypatch.setattr(background, 'run_background', lambda settings, local, **kwargs:
                        real_runner(settings, local, playwright_factory=s['factory'],
                                    notifier=s['notifier'], clock=lambda: s['now'][0], **kwargs))
    attempted = []
    def scan(*args, student_id, **kwargs):
        attempted.append(student_id)
        if student_id == 'danyal':
            raise background.AuthExpired()
        return RESULT.copy()
    s['scan'].side_effect = scan
    assert main(['scan-all-students']) == 1
    assert set(attempted) == set(s['configurations'])
    assert get_student(s['database'], 'danyal').auth_status == 'LOGIN_REQUIRED'
    for name in ('shahzad', 'muhammad-umar', 'talib'):
        assert get_student(s['database'], name).auth_status == 'ACTIVE'
    assert s['sender'].call_args.args[2] == 'danyal@example.edu'
    assert 'Failed students: danyal' in capsys.readouterr().out


def test_locks_are_shared_with_auth_but_not_other_students(students, monkeypatch):
    from app.main import main
    s = students
    wire_cli(s, monkeypatch)
    adapter = Mock()
    monkeypatch.setattr('app.lms.bahria.BahriaLMSAdapter.run', adapter)
    with background.scan_lock(s['configurations']['shahzad'].state_path.parent) as acquired:
        assert acquired
        assert s['run']() == 1
        assert main(['student-auth', 'shahzad']) == 1
        adapter.assert_not_called()
        assert s['run']('danyal') == 0


def test_browser_errors_and_failed_email_are_redacted(students, capsys, caplog):
    from playwright.sync_api import Error
    s = students
    s['scan'].side_effect = Error('cookies=SECRET token=SECRET password=SECRET')
    assert s['run']() == 1
    s['context'].close.assert_called_once()
    assert get_student(s['database'], 'shahzad').auth_status == 'UNKNOWN'
    s['scan'].side_effect = background.AuthExpired()
    s['sender'].side_effect = RuntimeError('password=SECRET')
    assert s['run']() == 1
    output = capsys.readouterr()
    assert 'SECRET' not in output.out + output.err + caplog.text
    with s['database']._connect() as connection:
        row = dict(connection.execute('SELECT * FROM notification_events').fetchone())
    assert row['state'] == 'failed'
    assert 'SECRET' not in json.dumps(row)


def test_profile_password_saving_disabled_and_permissions_private(students):
    s = students
    assert s['run']() == 0
    path = s['configurations']['shahzad'].profile_path
    prefs = json.loads((path / 'Default' / 'Preferences').read_text())
    assert prefs['credentials_enable_service'] is False
    assert prefs['profile']['password_manager_enabled'] is False
    assert prefs['autofill'] == {'profile_enabled': False, 'credit_card_enabled': False}
    for directory in (path.parent, path, path / 'Default'):
        assert directory.stat().st_mode & 0o777 == 0o700
    assert (path / 'Default' / 'Preferences').stat().st_mode & 0o777 == 0o600


def test_existing_profile_preserves_newer_cookies_and_storage(students):
    s = students
    assert s['run']() == 0
    cookie = dict(name='session', value='SYNTHETIC', domain='cms.bahria.edu.pk', path='/', expires=-1)
    save_state(s['configurations']['shahzad'].state_path, {'cookies': [cookie], 'origins': []})
    s['context'].set_storage_state.reset_mock()
    s['context'].cookies.return_value = [dict(cookie, value='NEWER')]
    assert s['run']() == 0
    s['context'].set_storage_state.assert_not_called()
    s['context'].add_cookies.assert_not_called()
    save_state(s['configurations']['shahzad'].state_path, {'cookies': [cookie], 'origins': []})
    s['context'].cookies.return_value = []
    assert s['run']() == 0
    s['context'].add_cookies.assert_called_once_with([cookie])


def test_profile_import_failure_preserves_snapshot_and_retries(students):
    from playwright.sync_api import Error
    s = students
    path = s['configurations']['shahzad'].state_path
    before = path.read_bytes()
    s['context'].set_storage_state.side_effect = Error('SECRET')
    assert s['run']() == 1
    assert path.read_bytes() == before
    assert not (s['configurations']['shahzad'].profile_path / '.initialized').exists()
    s['context'].close.assert_called_once()
    s['context'].set_storage_state.side_effect = None
    assert s['run']() == 0


@pytest.mark.parametrize('student_id', ['../shahzad', '/tmp/other', 'a/b', 'a\\b', '..'])
def test_unsafe_student_ids_rejected(students, student_id):
    with pytest.raises(ValueError, match='Student ID'):
        add_student(students['database'], 'Unsafe', 'synthetic@example.edu', student_id)


def test_shared_or_symlinked_student_path_rejected(students):
    root = students['local'].data_dir.parent
    student = get_student(students['database'], 'shahzad')
    with pytest.raises(LMSError, match='isolated'):
        student_session_path(replace(student, session_path='credentials/students/danyal/bahria_storage_state.json'), root)
    path = students['configurations']['shahzad'].profile_path
    path.symlink_to(students['configurations']['danyal'].state_path.parent, target_is_directory=True)
    assert students['run']() == 1
    students['factory'].return_value.__enter__.return_value.chromium.launch_persistent_context.assert_not_called()


def test_recovery_follows_only_real_bahria_link():
    context = MagicMock()
    page = context.new_page.return_value
    page.frames = []
    settings = LMSSettings('https://cms.bahria.edu.pk/', Path('/unused'))
    link = MagicMock()
    link.count.return_value = 1
    link.nth.return_value.is_visible.return_value = True
    link.nth.return_value.get_attribute.return_value = 'https://lms.bahria.edu.pk/SSO?token=SYNTHETIC'
    signin = MagicMock()
    signin.count.return_value = 0
    page.get_by_role.side_effect = lambda role, name, **kwargs: signin if isinstance(name, str) else link
    def navigate(url, **kwargs):
        page.url = url
        return Mock(status=200)
    page.goto.side_effect = navigate
    assert background.recover_session(context, settings)
    assert page.goto.call_count == 2
    page.close.assert_called_once()
    link.nth.return_value.get_attribute.return_value = 'https://attacker.example/steal'
    page.goto.reset_mock()
    assert not background.recover_session(context, settings)
    assert page.goto.call_count == 1


def test_student_auth_reuses_persistent_browser(students):
    from app.lms.bahria import BahriaLMSAdapter
    s = students
    page = s['context'].new_page.return_value
    s['context'].pages = []
    page.url = 'https://cms.bahria.edu.pk/dashboard'
    page.frames = []
    page.goto.return_value.status = 200
    # This unit checks browser lifecycle; CLI integration above checks reset.
    settings = s['configurations']['shahzad']
    background.target_path(settings).unlink()  # Synthetic fixture only.
    adapter = BahriaLMSAdapter(settings, playwright_factory=s['factory'], prompt=lambda _: 'yes')
    assert adapter.run('lms-auth') == 0
    options = s['factory'].return_value.__enter__.return_value.chromium.launch_persistent_context.call_args.kwargs
    assert options['headless'] is False
    s['context'].set_storage_state.assert_called_once_with(STATE)
    assert read_state(settings.state_path) == STATE


def test_debug_environment_disabled_during_browser_session(students, monkeypatch):
    import os
    s = students
    monkeypatch.setenv('DEBUG', 'pw:*')
    monkeypatch.setenv('PWDEBUG', '1')
    monkeypatch.setenv('DEBUG_FILE', '/tmp/should-not-be-created')
    def inspect_environment(*args, **kwargs):
        assert not any(name in os.environ for name in ('DEBUG', 'PWDEBUG', 'DEBUG_FILE'))
        return RESULT.copy()
    s['scan'].side_effect = inspect_environment
    assert s['run']() == 0
    assert os.environ['DEBUG'] == 'pw:*'


def test_genuine_expiry_during_table_extraction_is_auth_failure(monkeypatch):
    from app.lms.extraction import read_assignment_table
    monkeypatch.setattr('app.lms.extraction.login_page', lambda page: True)
    with pytest.raises(background.AuthExpired):
        read_assignment_table(Mock())
