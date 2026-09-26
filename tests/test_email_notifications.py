import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
import json
from unittest.mock import Mock

import pytest

from app import email_notifications as mail
from app.config import GmailSettings
from app.database import Database
from app.gmail_auth import GmailError, authenticate
from app.main import main
from app.models import Assignment, LOCAL_TIMEZONE, Source, Status
from app.notifier import NotificationError
from app.scheduler import check_reminders

NOW = datetime(2030, 9, 16, 12, tzinfo=LOCAL_TIMEZONE)


@pytest.fixture
def sender(monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    send = Mock()
    monkeypatch.setattr(mail, 'send_email', send)
    return send


def add(database, **changes):
    values = dict(course='Artificial Intelligence Lab', title='Lab 05',
                  source=Source.LMS, deadline=NOW + timedelta(days=1))
    values.update(changes)
    return database.create_assignment(Assignment(**values))


def queue(database, assignment, kind):
    with database._connect() as c:
        mail.enqueue_assignment(c, assignment, kind)


def test_plain_text_gmail_send(monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    service = Mock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {'id': 'synthetic'}
    monkeypatch.setattr('app.gmail_auth.gmail_service', Mock(return_value=service))
    mail.send_test_email()
    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    assert kwargs['userId'] == 'me'
    msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(kwargs['body']['raw']))
    assert msg['To'] == 'alerts@example.com'
    assert msg['Subject'] == 'Assignment Agent Test'
    assert msg.get_content_type() == 'text/plain'
    assert msg.get_content().strip() == 'Your Assignment Agent Gmail notifications are working.'
    service.users.return_value.messages.return_value.send.return_value.execute.assert_called_once()
    service.close.assert_called_once()


def test_old_token_requires_explicit_reauthorization(tmp_path, monkeypatch):
    settings = GmailSettings(tmp_path)
    settings.token_path.write_text(json.dumps({'scopes': ['https://www.googleapis.com/auth/gmail.readonly'], 'token': 'SECRET'}))
    factory = Mock()
    monkeypatch.setattr('app.gmail_auth.InstalledAppFlow.from_client_config', factory)
    with pytest.raises(GmailError, match='python -m app.main gmail-auth --force'):
        authenticate(settings, interactive=True)
    factory.assert_not_called()


def test_new_assignment_content_and_dedup(database, sender):
    a = add(database, lms_url='https://lms.bahria.edu.pk/Student/Assignments.php')
    queue(database, a, 'NEW_ASSIGNMENT')
    mail.deliver_pending(database, NOW)
    queue(database, a, 'NEW_ASSIGNMENT')
    mail.deliver_pending(Database(database.path), NOW)
    sender.assert_called_once()
    subject, body = sender.call_args.args
    assert subject == 'New Bahria Assignment — Artificial Intelligence Lab'
    for value in ('New assignment detected.', 'Lab 05', 'September 17, 2030', 'Not Submitted', a.lms_url):
        assert value in body
    with database._connect() as c:
        event = c.execute('SELECT * FROM notification_events').fetchone()
        assert event['assignment_id'] == a.id and event['channel'] == 'gmail'
        assert event['sent_at'] and event['state'] == 'sent'


@pytest.mark.parametrize('days,kind', [(1, 'Tomorrow'), (0, 'Today')])
def test_deadline_emails_and_duplicate_prevention(database, sender, days, kind):
    add(database, deadline=NOW + timedelta(days=days))
    desktop = Mock()
    check_reminders(database, desktop, NOW)
    check_reminders(database, desktop, NOW)
    sender.assert_called_once()
    subject, body = sender.call_args.args
    assert subject == f'Assignment Due {kind} — Lab 05'
    assert 'Course: Artificial Intelligence Lab' in body and 'Submission status: Not Submitted' in body
    desktop.send.assert_called_once()


@pytest.mark.parametrize('days', [0, 1, 3, -1])
def test_submitted_no_deadline_email(database, sender, days):
    add(database, status=Status.SUBMITTED, deadline=NOW + timedelta(days=days))
    check_reminders(database, Mock(), NOW)
    sender.assert_not_called()


def test_submission_cancels_pending_and_future_events(database, sender):
    a = add(database)
    queue(database, a, 'NEW_ASSIGNMENT')
    database.mark_submitted(a.id)
    mail.deliver_pending(database, NOW)
    check_reminders(database, Mock(), NOW)
    check_reminders(database, Mock(), NOW + timedelta(days=1))
    sender.assert_not_called()


def test_two_days_only_and_no_replay_after_submission(database, sender):
    a = add(database)
    for days in (-2, 0, 1, 2):
        check_reminders(database, Mock(), NOW + timedelta(days=days))
    assert sender.call_count == 2
    database.mark_submitted(a.id)
    check_reminders(database, Mock(), NOW + timedelta(days=1))
    assert sender.call_count == 2


def test_email_failure_does_not_block_desktop_or_retry(database, sender, caplog):
    add(database)
    sender.side_effect = RuntimeError('SECRET_TOKEN')
    desktop = Mock()
    assert check_reminders(database, desktop, NOW).sent == 1
    check_reminders(database, desktop, NOW)
    desktop.send.assert_called_once()
    sender.assert_called_once()
    assert 'SECRET_TOKEN' not in caplog.text
    with database._connect() as c:
        assert c.execute('SELECT state FROM notification_events').fetchone()[0] == 'failed'


def test_desktop_failure_does_not_block_email(database, sender):
    add(database)
    desktop = Mock()
    desktop.send.side_effect = NotificationError('Desktop unavailable')
    assert check_reminders(database, desktop, NOW).errors
    sender.assert_called_once()
    desktop.send.side_effect = None
    check_reminders(database, desktop, NOW)
    sender.assert_called_once()


def test_concurrent_email_claim_once(database, sender):
    a = add(database)
    queue(database, a, 'NEW_ASSIGNMENT')
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: mail.deliver_pending(database, NOW), range(2)))
    sender.assert_called_once()


def test_test_email_cli(sender):
    assert main(['test-email']) == 0
    sender.assert_called_once_with('Assignment Agent Test', 'Your Assignment Agent Gmail notifications are working.')


def test_test_email_missing_recipient(capsys):
    assert main(['test-email']) == 1
    assert 'ALERT_EMAIL' in capsys.readouterr().err


def test_auth_email_cooldown(database, sender):
    epoch = NOW.timestamp()
    mail.notify_auth_expired(database.path, 'fingerprint', epoch)
    mail.notify_auth_expired(database.path, 'fingerprint', epoch + 1800)
    sender.assert_called_once()
    assert sender.call_args.args[0] == 'Bahria LMS Login Required'
    assert 'python -m app.main lms-auth' in sender.call_args.args[1]
    mail.notify_auth_expired(database.path, 'fingerprint', epoch + 86400)
    assert sender.call_count == 2


def test_disabled_email_no_retrospective_new_events(database, monkeypatch):
    send = Mock()
    monkeypatch.setattr(mail, 'send_email', send)
    a = add(database)
    queue(database, a, 'NEW_ASSIGNMENT')
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    mail.deliver_pending(database, NOW)
    send.assert_not_called()


def test_ambiguous_send_not_retried(monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL', 'alerts@example.com')
    service = Mock()
    request = service.users.return_value.messages.return_value.send.return_value
    request.execute.side_effect = OSError('SECRET')
    monkeypatch.setattr('app.gmail_auth.gmail_service', Mock(return_value=service))
    with pytest.raises(GmailError) as error:
        mail.send_test_email()
    assert 'SECRET' not in str(error.value)
    request.execute.assert_called_once()
