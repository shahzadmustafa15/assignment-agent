"""Unattended scans with temporary SQLite and mocked browsers/notifications."""
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from app.config import Settings
from app.database import Database
from app.lms import background
from app.lms.base import LMSSettings, LMSError
from app.lms.extraction import HEADERS
from app.lms.session import save_state
from app.models import Status
from app.notifier import NotificationError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = LMSSettings('https://cms.bahria.edu.pk/', tmp_path / 'credentials/state.json')
    local = Settings(tmp_path / 'data', tmp_path / 'logs')
    save_state(settings.state_path, {'cookies': [], 'origins': []})
    target = {'url': 'https://lms.bahria.edu.pk/Student/Assignments.php?course=synthetic',
              'course': 'Synthetic course', 'semester': 'Fall 2026'}
    save_state(background.target_path(settings), target)
    values = ['3', 'Lab 03', '', 'No Submission', '', '', 'Deadline Exceeded',
              '23 September 2026-09:00 am']
    table = {'headers': list(HEADERS), 'rows': [[{'text': v} for v in values]],
             'course': target['course'], 'semester': target['semester'], 'page_url': target['url']}
    fetch = Mock(return_value=table)
    monkeypatch.setattr(background, 'fetch_table', fetch)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.storage_state.return_value = {'cookies': [], 'origins': []}
    notifier = Mock()
    now = [1_800_000_000.0]
    def run(**kwargs):
        return background.run_background(settings, local, notifier=notifier,
                    playwright_factory=factory, clock=lambda: now[0], **kwargs)
    return settings, local, table, fetch, factory, notifier, now, run


def test_background_dedup_and_notification_once(setup, capsys):
    settings, local, table, fetch, factory, notifier, now, run = setup
    assert run() == run() == 0
    rows = Database(local.database_path).list_assignments()
    assert len(rows) == 1
    notifier.send.assert_called_once()
    assert notifier.send.call_args.args[0] == 'New Assignment Detected'
    output = capsys.readouterr().out
    assert '"new": 1' in output and '"existing": 1' in output
    assert '"updated": 0' in output
    assert 'scan_started' in output and 'authentication_valid' in output and 'scan_completed' in output
    options = factory.return_value.__enter__.return_value.chromium.launch.call_args.kwargs
    assert options['headless'] is True


def test_background_metadata_update_not_new(setup, capsys):
    settings, local, table, fetch, factory, notifier, now, run = setup
    assert run() == 0
    table['rows'][0][3]['text'] = 'Submitted'
    table['rows'][0][4]['text'] = '9 / 10'
    table['rows'][0][5]['text'] = 'Good work'
    assert run() == 0
    db = Database(local.database_path)
    assert len(db.list_assignments()) == 1
    assert db.list_assignments()[0].status is Status.SUBMITTED
    with db._connect() as connection:
        metadata = json.loads(connection.execute('SELECT metadata FROM lms_records').fetchone()[0])
    assert metadata['marks'] == '9 / 10' and metadata['returned_comments'] == 'Good work'
    notifier.send.assert_called_once()
    assert '"updated": 1' in capsys.readouterr().out
    assert run() == 0
    assert '"updated": 0' in capsys.readouterr().out


def test_expired_auth_exits_safely_and_cooldown_skips_network(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    fetch.side_effect = background.AuthExpired('secret must not be printed')
    assert run() == 0
    assert not local.database_path.exists()
    assert notifier.send.call_args.args[0] == 'Bahria LMS login required'
    now[0] += 1800
    factory.reset_mock()
    assert run() == 0
    factory.assert_not_called()
    notifier.send.assert_called_once()
    assert fetch.call_count == 1
    now[0] += background.AUTH_COOLDOWN
    assert run() == 0
    assert fetch.call_count == 2 and notifier.send.call_count == 2


def test_restored_auth_resets_cooldown(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    fetch.side_effect = background.AuthExpired()
    assert run() == 0
    save_state(settings.state_path, {'cookies': [], 'origins': [], 'renewed': True})
    fetch.side_effect = None
    now[0] += 60
    assert run() == 0
    assert json.loads((local.data_dir / 'lms-auth-notice.json').read_text()) == {}
    notifier.reset_mock()
    fetch.side_effect = background.AuthExpired()
    assert run() == 0
    notifier.send.assert_called_once()


def test_missing_auth_no_browser_or_database(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    settings.state_path.unlink()
    assert run() == 0
    factory.assert_not_called()
    assert not local.database_path.exists()
    notifier.send.assert_called_once()


def test_auth_notification_failure_is_bounded(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    fetch.side_effect = background.AuthExpired()
    notifier.send.side_effect = NotificationError('unavailable')
    assert run() == 0
    assert run() == 0
    assert fetch.call_count == notifier.send.call_count == 1


def test_background_errors_sanitized_and_browser_closed(setup, capsys):
    from playwright.sync_api import Error
    settings, local, table, fetch, factory, notifier, now, run = setup
    fetch.side_effect = Error('https://example.org/?token=PRIVATE cookies=PRIVATE')
    assert run() == 1
    output = capsys.readouterr().out
    assert 'PRIVATE' not in output and 'scan_error' in output and 'scan_completed' in output
    factory.return_value.__enter__.return_value.chromium.launch.return_value.close.assert_called_once()
    notifier.send.assert_not_called()


def test_background_dry_run_no_db_or_notices(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    before = settings.state_path.read_bytes()
    assert run(dry_run=True) == 0
    assert not local.database_path.exists()
    assert not (local.data_dir / 'lms-auth-notice.json').exists()
    assert settings.state_path.read_bytes() == before
    notifier.send.assert_not_called()
    fetch.side_effect = background.AuthExpired()
    assert run(dry_run=True) == 0
    notifier.send.assert_not_called()
    assert not (local.data_dir / 'lms-auth-notice.json').exists()


def test_background_concurrent_scan_skips(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    with background.scan_lock(local.data_dir) as acquired:
        assert acquired
        assert run() == 0
    factory.assert_not_called()


def test_background_cli_dispatch(setup, monkeypatch):
    from app.main import main
    settings, local, *_ = setup
    monkeypatch.setenv('ASSIGNMENT_LMS_BACKGROUND', '1')
    monkeypatch.setattr('app.lms.base.load_settings', lambda: settings)
    runner = Mock(return_value=0)
    monkeypatch.setattr(background, 'run_background', runner)
    assert main(['lms-scan']) == 0
    assert runner.call_args.args[0] == settings
    assert runner.call_args.kwargs == {'dry_run': False, 'multi_course': True, 'verbose': False}


def test_systemd_virtualenv_paths_and_schedule():
    root = Path(__file__).resolve().parents[1]
    service = (root / 'deploy/systemd/assignment-agent-lms-scan.service').read_text()
    timer = (root / 'deploy/systemd/assignment-agent-lms-scan.timer').read_text()
    assert 'WorkingDirectory=%h/Desktop/assignment-agent' in service
    assert 'ExecStart=%h/Desktop/assignment-agent/.venv/bin/python -m app.main lms-scan\n' in service
    assert 'ASSIGNMENT_LMS_BACKGROUND=1' in service
    assert 'TimeoutStartSec=3min' in service
    assert 'OnCalendar=*-*-* *:00,30:00' in timer
    assert 'Persistent=true' in timer
    assert '5min' not in timer
    assert (root / '.venv/bin/python').exists()


def test_target_configuration_verified_before_save(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    page = Mock(url=table['page_url'])
    context = Mock()
    context.storage_state.return_value = {'cookies': [], 'origins': [], 'lms': True}
    background.configure_target(settings, context, page, table)
    assert background.load_target(settings)['course'] == table['course']
    assert json.loads(settings.state_path.read_text())['lms']
    assert background.target_path(settings).stat().st_mode & 0o777 == 0o600
    before = settings.state_path.read_bytes()
    fetch.side_effect = LMSError('cannot replay')
    with pytest.raises(LMSError):
        background.configure_target(settings, context, page, table)
    assert settings.state_path.read_bytes() == before


def test_fetch_table_rejects_changed_course(monkeypatch):
    # Exercise the real fetch function independently of the runner fixture.
    context = MagicMock()
    page = context.new_page.return_value
    page.url = 'https://lms.bahria.edu.pk/Student/Assignments.php'
    page.goto.return_value.status = 200
    monkeypatch.setattr(background, 'check_auth', Mock())
    monkeypatch.setattr(background, 'read_assignment_table', Mock(return_value={'course': 'Wrong', 'semester': 'Fall'}))
    with pytest.raises(LMSError, match='course/semester'):
        background.fetch_table(context, {'url': page.url, 'course': 'Right', 'semester': 'Fall'})
    page.close.assert_called_once()


@pytest.mark.parametrize('status', [401, 403])
def test_auth_http_response(status):
    with pytest.raises(background.AuthExpired):
        background.check_auth(Mock(), Mock(status=status))


def test_signin_landing_is_expired(monkeypatch):
    monkeypatch.setattr(background, 'login_page', lambda _: False)
    page = MagicMock()
    page.get_by_role.return_value.count.return_value = 1
    page.get_by_role.return_value.first.is_visible.return_value = True
    with pytest.raises(background.AuthExpired):
        background.check_auth(page, Mock(status=200))


def test_private_auth_notice_and_no_secret_logs(setup, capsys):
    settings, local, table, fetch, factory, notifier, now, run = setup
    fetch.side_effect = background.AuthExpired('cookies=SECRET')
    assert run() == 0
    path = local.data_dir / 'lms-auth-notice.json'
    assert path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert 'SECRET' not in output
    assert 'fingerprint' not in output


def test_changed_unknown_deadline_never_silently_duplicates(setup):
    settings, local, table, fetch, factory, notifier, now, run = setup
    assert run() == 0
    table['rows'][0][-1]['text'] = '24 September 2026-09:00 am'
    assert run() == 1
    assert len(Database(local.database_path).list_assignments()) == 1
    notifier.send.assert_called_once()


def test_dry_run_cannot_configure_background(monkeypatch):
    from app.main import main
    with pytest.raises(SystemExit) as error:
        main(['lms-scan', '--dry-run', '--configure-background'])
    assert error.value.code == 2


def test_email_new_event_failure_preserves_lms_scan(setup, monkeypatch, caplog):
    settings, local, table, fetch, factory, notifier, now, run = setup
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock(side_effect=RuntimeError('SECRET_TOKEN'))
    monkeypatch.setattr('app.email_notifications.send_email', send)
    assert run() == 0
    assert run() == 0
    send.assert_called_once()
    notifier.send.assert_called_once()
    assert len(Database(local.database_path).list_assignments()) == 1
    assert 'SECRET_TOKEN' not in caplog.text


def test_auth_email_uses_existing_cooldown_and_no_dry_run_send(setup, monkeypatch):
    settings, local, table, fetch, factory, notifier, now, run = setup
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock()
    monkeypatch.setattr('app.email_notifications.send_email', send)
    fetch.side_effect = background.AuthExpired('secret')
    assert run(dry_run=True) == 0
    send.assert_not_called()
    assert not local.database_path.exists()
    assert run() == 0
    now[0] += 1800
    assert run() == 0
    send.assert_called_once()
    assert send.call_args.args[0] == 'Bahria LMS Login Required'
    now[0] += background.AUTH_COOLDOWN
    assert run() == 0
    assert send.call_count == 2


def test_new_email_independent_of_desktop_failure(setup, monkeypatch):
    settings, local, table, fetch, factory, notifier, now, run = setup
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock()
    monkeypatch.setattr('app.email_notifications.send_email', send)
    notifier.send.side_effect = NotificationError('Desktop unavailable')
    assert run() == 1
    send.assert_called_once()
    notifier.send.side_effect = None
    assert run() == 0
    send.assert_called_once()


def test_new_email_success_no_existing_resend_or_dry_run_email(setup, monkeypatch):
    settings, local, table, fetch, factory, notifier, now, run = setup
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock()
    monkeypatch.setattr('app.email_notifications.send_email', send)
    assert run(dry_run=True) == 0
    send.assert_not_called()
    assert not local.database_path.exists()
    assert run() == 0
    assert run() == 0
    send.assert_called_once()
    assert send.call_args.args[0] == 'New Bahria Assignment — Synthetic course'


def test_new_submitted_record_has_no_email(setup, monkeypatch):
    settings, local, table, fetch, factory, notifier, now, run = setup
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock()
    monkeypatch.setattr('app.email_notifications.send_email', send)
    table['rows'][0][3]['text'] = 'Submitted'
    assert run() == 0
    assert Database(local.database_path).list_assignments()[0].status is Status.SUBMITTED
    send.assert_not_called()
