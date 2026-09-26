"""Local deletion only: temporary SQLite, with network blocked by conftest."""

from unittest.mock import Mock

import pytest

from app.config import load_settings
from app.database import Database
from app.main import main
from app.models import Assignment


@pytest.fixture
def records():
    database = Database(load_settings().database_path)
    target = database.create_assignment(Assignment(course="Synthetic", title="Fee Payment Due Date Passed"))
    other = database.create_assignment(Assignment(course="Synthetic", title="Keep this assignment"))
    return database, target, other


@pytest.mark.parametrize("answer", ["y", " YES "])
def test_confirmed_deletion(records, monkeypatch, capsys, answer):
    database, target, other = records
    confirm = Mock(return_value=answer)
    monkeypatch.setattr("builtins.input", confirm)
    assert main(["delete", str(target.id)]) == 0
    confirm.assert_called_once_with(f'Delete assignment {target.id}: "Fee Payment Due Date Passed"? [y/N] ')
    assert database.get_assignment(target.id) is None
    assert database.get_assignment(other.id) == other
    assert "Deleted local assignment" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["n", "", "no", "anything else"])
def test_confirmation_declined(records, monkeypatch, capsys, answer):
    database, target, other = records
    monkeypatch.setattr("builtins.input", Mock(return_value=answer))
    assert main(["delete", str(target.id)]) == 0
    assert database.get_assignment(target.id) == target
    assert database.get_assignment(other.id) == other
    assert "cancelled" in capsys.readouterr().out


def test_yes_skips_prompt_and_external_handlers(records, monkeypatch):
    database, target, other = records
    prompt = Mock(side_effect=AssertionError("Unexpected prompt"))
    external = Mock(side_effect=AssertionError("Unexpected external command"))
    monkeypatch.setattr("builtins.input", prompt)
    monkeypatch.setattr("app.main.gmail_command", external)
    assert main(["delete", str(target.id), "--yes"]) == 0
    prompt.assert_not_called()
    external.assert_not_called()
    assert database.get_assignment(target.id) is None
    assert database.get_assignment(other.id) == other


@pytest.mark.parametrize("flags", [[], ["--yes"]])
def test_missing_id(records, monkeypatch, capsys, flags):
    database, target, other = records
    prompt = Mock(side_effect=AssertionError("Missing ID must not prompt"))
    monkeypatch.setattr("builtins.input", prompt)
    assert main(["delete", "999999", *flags]) == 1
    assert "Assignment 999999 not found; nothing deleted" in capsys.readouterr().err
    assert database.get_assignment(target.id) == target
    assert database.get_assignment(other.id) == other
    prompt.assert_not_called()


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_interrupted_confirmation_is_safe(records, monkeypatch, capsys, interruption):
    database, target, _ = records
    monkeypatch.setattr("builtins.input", Mock(side_effect=interruption))
    assert main(["delete", str(target.id)]) == 0
    assert database.get_assignment(target.id) == target
    assert "cancelled" in capsys.readouterr().out
