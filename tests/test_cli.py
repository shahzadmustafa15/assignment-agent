from datetime import timedelta

from app.config import load_settings
from app.database import Database
from app.main import main
from app.models import Assignment, Status, utc_now


def test_cli_commands(tmp_path, capsys):
    assert load_settings().database_path == tmp_path / "data" / "assignments.db"
    assert main([]) == 0
    assert "Phase 3" in capsys.readouterr().out
    database = Database(load_settings().database_path)
    future = database.create_assignment(Assignment(course="Synthetic", title="Future task",
                                                   deadline=utc_now() + timedelta(days=2)))
    past = database.create_assignment(Assignment(course="Synthetic", title="Past task",
                                                 deadline=utc_now() - timedelta(days=2)))
    for command in ("assignments", "new", "upcoming"):
        assert main([command]) == 0
        output = capsys.readouterr().out
        assert "Future task" in output
        if command == "upcoming":
            assert "Past task" not in output
    assert main(["details", str(future.id)]) == 0
    assert "+05:00" in capsys.readouterr().out
    assert main(["overdue"]) == 0
    assert "Past task" in capsys.readouterr().out
    assert database.get_assignment(past.id).status is Status.OVERDUE
    assert main(["mark-submitted", str(future.id)]) == 0
    assert "no files were submitted" in capsys.readouterr().out
    assert database.get_assignment(future.id).status is Status.SUBMITTED
    assert main(["details", "9999"]) == 1
    assert "not found" in capsys.readouterr().err


def test_empty_listing_and_invalid_configuration(monkeypatch, capsys):
    assert main(["assignments"]) == 0
    assert "No assignments" in capsys.readouterr().out
    monkeypatch.setenv("ASSIGNMENT_DATA_DIR", " ")
    assert main([]) == 1
    assert "must not be empty" in capsys.readouterr().err


def test_notification_and_summary_commands(monkeypatch, capsys):
    from unittest.mock import Mock
    from app.notifier import NotificationError

    send = Mock()
    monkeypatch.setattr("app.main.DesktopNotifier.send", send)
    assert main(["test-notification"]) == 0
    send.assert_called_once()
    assert not load_settings().database_path.exists()
    send.reset_mock()
    assert main(["daily-summary"]) == 0
    assert "ASSIGNMENT SUMMARY" in capsys.readouterr().out
    send.assert_not_called()
    assert main(["daily-summary", "--notify"]) == 0
    send.assert_called_once()
    database = Database(load_settings().database_path)
    database.create_assignment(Assignment(course="Synthetic", title="Notify task",
                                          deadline=utc_now() + timedelta(minutes=30)))
    send.reset_mock()
    send.side_effect = NotificationError("Synthetic failure")
    assert main(["check-reminders"]) == 1
    assert "Synthetic failure" in capsys.readouterr().err
    send.side_effect = None
    assert main(["check-reminders"]) == 0
    assert "Reminders sent: 1" in capsys.readouterr().out
    assert main(["check-reminders"]) == 0
    assert "Reminders sent: 0" in capsys.readouterr().out
    send.side_effect = NotificationError("Synthetic failure")
    assert main(["test-notification"]) == 1
