import subprocess
from unittest.mock import Mock

import pytest

from app.notifier import DesktopNotifier, NotificationError


def test_notify_send_arguments(monkeypatch):
    monkeypatch.setattr("app.notifier.shutil.which", lambda _: "/usr/bin/notify-send")
    run = Mock()
    monkeypatch.setattr("app.notifier.subprocess.run", run)
    DesktopNotifier().send("--synthetic-title", "<task> & description", urgent=True)
    arguments = run.call_args.args[0]
    assert arguments == ["/usr/bin/notify-send", "--app-name=Assignment Agent", "--urgency=critical",
                         "--", "--synthetic-title", "&lt;task&gt; &amp; description"]
    assert run.call_args.kwargs["timeout"] == 10
    assert run.call_args.kwargs["check"] is True
    assert "shell" not in run.call_args.kwargs


def test_missing_notify_send(monkeypatch):
    monkeypatch.setattr("app.notifier.shutil.which", lambda _: None)
    with pytest.raises(NotificationError, match="libnotify-bin"):
        DesktopNotifier().send("Test", "Body")


@pytest.mark.parametrize("error", [OSError("Missing"), subprocess.TimeoutExpired("notify-send", 10),
                                   subprocess.CalledProcessError(1, "notify-send")])
def test_delivery_failure(monkeypatch, error):
    monkeypatch.setattr("app.notifier.shutil.which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr("app.notifier.subprocess.run", Mock(side_effect=error))
    with pytest.raises(NotificationError, match="no reminder was recorded"):
        DesktopNotifier().send("Test", "Body")


def test_vps_journal_notification_without_desktop(monkeypatch, capsys):
    import json
    monkeypatch.setenv('ASSIGNMENT_NOTIFICATION_DESTINATION', 'journal')
    DesktopNotifier().send('Assignment Due Today', 'Synthetic task\nCourse: Test', urgent=True)
    event = json.loads(capsys.readouterr().out)
    assert event == {'event': 'local_notification', 'title': 'Assignment Due Today',
                     'body': 'Synthetic task\nCourse: Test', 'urgent': True}
